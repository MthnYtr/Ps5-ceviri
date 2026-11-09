"""
PS5 Çeviri - ekrandaki İngilizce yazıyı okuyup Türkçeye çeviren kişisel araç.

Akış:  görüntü kaynağı  ->  seçilen alanı kırp  ->  OCR  ->  çeviri  ->  telefon sayfası / TV altyazısı

Örnekler:
  python app.py --source card --mode phone      (PS5 -> kart -> TV, çeviri telefonda)
  python app.py --source card --mode tv         (PS5 -> kart -> PC -> TV, altyazı görüntünün üstünde)
  python app.py --source screen                 (Remote Play penceresinden, çeviri telefonda)
  python app.py --select-roi                    (altyazı alanını yeniden seç)
"""

import argparse
import difflib
import json
import os
import re
import socket
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


# --------------------------------------------------------------------------- #
# Ayarlar
# --------------------------------------------------------------------------- #
def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Görüntü kaynakları
# --------------------------------------------------------------------------- #
class CardSource:
    """Capture kartı. Arka planda sürekli kare okur, en sonuncuyu tutar."""

    def __init__(self, index, width=1920, height=1080, fps=60):
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Capture kartı açılamadı (cihaz no: {index}). "
                "Başka bir program (OBS vb.) kartı kullanıyor olabilir ya da --device 1 deneyin."
            )
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[kart] açıldı: {w}x{h}")

    def _loop(self):
        fails = 0
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                fails += 1
                if fails == 50:
                    print("[kart] görüntü gelmiyor. PS5'te HDCP kapalı mı? Kablolar doğru mu?")
                time.sleep(0.01)
                continue
            fails = 0
            with self.lock:
                self.frame = frame
                self.frame_id += 1

    def latest(self):
        with self.lock:
            return self.frame, self.frame_id

    def full_frame(self, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            f, _ = self.latest()
            if f is not None:
                return f.copy()
            time.sleep(0.05)
        raise RuntimeError("Karttan görüntü alınamadı. PS5 açık mı, HDCP kapalı mı?")

    def grab(self, roi):
        f, _ = self.latest()
        if f is None:
            return None
        x, y, w, h = roi
        return f[y:y + h, x:x + w].copy()

    def close(self):
        self.running = False
        time.sleep(0.05)
        self.cap.release()


def _new_mss():
    import mss
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


class ScreenSource:
    """Ekran yakalama (Remote Play penceresi için). Sadece seçilen alanı yakalar."""

    def __init__(self, monitor=1):
        self.monitor_index = monitor
        self._local = threading.local()
        with _new_mss() as s:
            mon = s.monitors[monitor]
        self.mon = mon
        print(f"[ekran] monitör {monitor}: {mon['width']}x{mon['height']}")

    def _sct(self):
        if not hasattr(self._local, "sct"):
            self._local.sct = _new_mss()
        return self._local.sct

    def full_frame(self):
        img = np.array(self._sct().grab(self.mon))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def grab(self, roi):
        x, y, w, h = roi
        box = {"left": self.mon["left"] + x, "top": self.mon["top"] + y, "width": w, "height": h}
        img = np.array(self._sct().grab(box))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def latest(self):
        return None, 0

    def close(self):
        pass


class ImageSource:
    """Test için: bir resim dosyası."""

    def __init__(self, path):
        self.img = cv2.imread(path)
        if self.img is None:
            raise RuntimeError(f"Resim okunamadı: {path}")

    def full_frame(self):
        return self.img.copy()

    def grab(self, roi):
        x, y, w, h = roi
        return self.img[y:y + h, x:x + w].copy()

    def latest(self):
        return self.img, 1

    def close(self):
        pass


def select_roi(frame):
    """Altyazı alanını fareyle seçtirir. Büyük kareleri ekrana sığdırır."""
    h, w = frame.shape[:2]
    scale = min(1.0, 1280 / w, 720 / h)
    view = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
    title = "Altyazi alanini secin, ENTER ile onaylayin (ESC = iptal)"
    r = cv2.selectROI(title, view, showCrosshair=False)
    cv2.destroyWindow(title)
    x, y, rw, rh = [int(v / scale) for v in r]
    if rw < 10 or rh < 10:
        return None
    return [x, y, rw, rh]


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
def _torch_dll_dir():
    """Windows'ta CUDA DLL'leri torch ile birlikte gelir; diğer kütüphaneler de oradan yüklesin."""
    try:
        import torch
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if sys.platform == "win32" and os.path.isdir(lib):
            os.add_dll_directory(lib)
        return torch
    except Exception:
        return None


def _patch_ort_heuristic(ort):
    """rapidocr CUDA'da 'EXHAUSTIVE' cuDNN araması kullanıyor; her yeni görüntü boyutunda
    yüzlerce ms kaybettirir. 'HEURISTIC' ile değiştir."""
    if getattr(ort.InferenceSession, "_ps5_patched", False):
        return
    orig = ort.InferenceSession.__init__

    def patched(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kw):
        if providers:
            fixed = []
            for pr in providers:
                if isinstance(pr, tuple) and pr[0] == "CUDAExecutionProvider":
                    pr = (pr[0], {**pr[1], "cudnn_conv_algo_search": "HEURISTIC"})
                fixed.append(pr)
            providers = fixed
        orig(self, path_or_bytes, sess_options, providers, provider_options, **kw)

    ort.InferenceSession.__init__ = patched
    ort.InferenceSession._ps5_patched = True


class OCR:
    def __init__(self, min_score=0.75, use_gpu=True):
        from rapidocr_onnxruntime import RapidOCR
        self.min_score = min_score
        kwargs = {}
        if use_gpu:
            try:
                _torch_dll_dir()          # önce torch: CUDA/cuDNN DLL'leri yüklensin
                import onnxruntime as ort
                if "CUDAExecutionProvider" in ort.get_available_providers():
                    _patch_ort_heuristic(ort)
                    kwargs = dict(det_use_cuda=True, cls_use_cuda=True, rec_use_cuda=True)
                else:
                    print("[ocr] onnxruntime-gpu kurulu değil -> işlemci kullanılıyor (BENIOKU: Hızlandırma)")
            except Exception as e:
                print(f"[ocr] GPU kontrolü başarısız: {e}")
        try:
            self.engine = RapidOCR(**kwargs)
        except Exception as e:
            if not kwargs:
                raise
            print(f"[ocr] GPU ile başlatılamadı ({e}) -> işlemci")
            self.engine = RapidOCR()
        try:
            dev = self.engine.text_det.infer.session.get_providers()[0]
        except Exception:
            dev = "CPUExecutionProvider"
        self.device = "GPU" if "CUDA" in dev else "CPU"
        print(f"[ocr] hazır ({self.device})")
        self._no_cls = True

    def _run(self, img):
        # Altyazılar hep düz durur; döndürme sınıflandırıcısını atla (hız)
        if self._no_cls:
            try:
                return self.engine(img, use_cls=False)
            except TypeError:
                self._no_cls = False
        return self.engine(img)

    def read(self, img):
        self.last_top = None   # okunan yazının üst kenarı (alan içindeki piksel)
        h, w = img.shape[:2]
        scale = 1.0
        # Çok geniş alanları küçült (hız), çok ince alanları büyüt (doğruluk)
        if w > 1280:
            scale = 1280 / w
        elif h < 40:
            scale = 40 / h
        if scale != 1.0:
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)
        h, w = img.shape[:2]
        # Çerçeve ekle: kenara yapışık yazılar da algılansın ve OCR, alanı
        # "tek satır" sanıp yazı tespitini atlamasın (en/boy oranı <= 2).
        pad = 32
        extra = max(0, (w + 2 * pad) // 2 - (h + 2 * pad))
        top = pad + extra // 2
        bottom = pad + extra - extra // 2
        color = tuple(int(v) for v in np.median(img.reshape(-1, 3), axis=0))
        img = cv2.copyMakeBorder(img, top, bottom, pad, pad, cv2.BORDER_CONSTANT, value=color)
        pad_top = top
        result, _ = self._run(img)
        if not result:
            return ""
        items = []
        for box, text, score in result:
            if float(score) < self.min_score or len(text.strip()) < 2:
                continue
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append((min(ys), max(ys), min(xs), text.strip()))
        if not items:
            return ""
        self.last_top = max(0.0, (min(t[0] for t in items) - pad_top) / scale)
        # Satırlara grupla (üstten alta, soldan sağa)
        items.sort(key=lambda t: (t[0], t[2]))
        lines, cur, cur_bottom = [], [], None
        for top, bottom, left, text in items:
            if cur and top > cur_bottom - (bottom - top) * 0.4:
                lines.append(cur)
                cur, cur_bottom = [], None
            cur.append((left, text))
            cur_bottom = bottom if cur_bottom is None else max(cur_bottom, bottom)
        if cur:
            lines.append(cur)
        out = [" ".join(t for _, t in sorted(line)) for line in lines]
        return strip_button_prompts(" ".join(out))


# Tuş ipuçları: "R1 [Hold] Throw", "L1+R1 (Hold) Pull", "[Hold) Throw" ...
_BTN = r"(?:L[123]|R[123])"
_PROMPT_RES = [
    re.compile(rf"(?:\b{_BTN}\b[\s+/]*)+[\[\(]*\s*(?:hold|press|tap|mash)?\s*[\]\)]*\s*[A-Za-z]+", re.I),
    re.compile(r"[\[\(]+\s*(?:hold|press|tap|mash)\s*[\]\)]+\s*[A-Za-z]*", re.I),
]


def strip_button_prompts(text):
    for rx in _PROMPT_RES:
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Çeviri
# --------------------------------------------------------------------------- #
class FastLocalTranslator:
    """Aynı Helsinki-NLP modeli, CTranslate2 ile (~3 kat hızlı, kalite aynı).
    İlk açılışta model bir kereliğine dönüştürülür (models/ klasörü)."""

    MODEL = "Helsinki-NLP/opus-mt-tc-big-en-tr"

    def __init__(self, beam_size=3):
        torch = _torch_dll_dir()
        import ctranslate2
        from transformers import MarianTokenizer
        out_dir = BASE_DIR / "models" / "opus-mt-tc-big-en-tr-ct2"
        if not (out_dir / "model.bin").exists():
            print("[çeviri] model hızlı formata dönüştürülüyor (bir kereliğine, ~1 dk)...")
            from ctranslate2.converters import TransformersConverter
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            TransformersConverter(self.MODEL).convert(str(out_dir), quantization="float16", force=True)
        cuda = ctranslate2.get_cuda_device_count() > 0
        self.device = "cuda" if cuda else "cpu"
        compute = "float16" if cuda else "int8"
        self.tok = MarianTokenizer.from_pretrained(self.MODEL)
        self.model = ctranslate2.Translator(str(out_dir), device=self.device, compute_type=compute)
        self.beam = beam_size
        print(f"[çeviri] hızlı model yükleniyor ({self.device}, {compute})")
        self.translate("Hello.")
        print("[çeviri] hazır.")

    def translate(self, text):
        sentences = split_sentences(text)
        batch = [self.tok.convert_ids_to_tokens(self.tok.encode(s_)) for s_ in sentences]
        res = self.model.translate_batch(batch, beam_size=self.beam, max_decoding_length=256)
        outs = []
        for r in res:
            ids = self.tok.convert_tokens_to_ids(r.hypotheses[0])
            outs.append(self.tok.decode(ids, skip_special_tokens=True))
        return " ".join(outs)


def make_local_translator(cfg):
    if cfg.get("fast_translate", True):
        try:
            return FastLocalTranslator()
        except ImportError:
            print("[çeviri] ctranslate2 kurulu değil -> normal mod (BENIOKU: Hızlandırma)")
        except Exception as e:
            print(f"[çeviri] hızlı mod açılamadı ({type(e).__name__}: {e}) -> normal mod")
    return LocalTranslator()


class LocalTranslator:
    """Helsinki-NLP opus-mt-tc-big-en-tr. Bilgisayarda, internetsiz çalışır (ilk açılışta indirilir)."""

    MODEL = "Helsinki-NLP/opus-mt-tc-big-en-tr"

    def __init__(self):
        import torch
        from transformers import MarianMTModel, MarianTokenizer
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[çeviri] model yükleniyor ({self.device})... ilk seferde ~1 GB indirilir.")
        self.tok = MarianTokenizer.from_pretrained(self.MODEL)
        self.model = MarianMTModel.from_pretrained(self.MODEL)
        if self.device == "cuda":
            self.model = self.model.half()
        self.model.to(self.device).eval()
        self.translate("Hello.")  # ısınma
        print("[çeviri] hazır.")

    def translate(self, text):
        sentences = split_sentences(text)
        with self.torch.inference_mode():
            batch = self.tok(sentences, return_tensors="pt", padding=True, truncation=True, max_length=256)
            batch = {k: v.to(self.device) for k, v in batch.items()}
            out = self.model.generate(**batch, num_beams=3, max_new_tokens=256)
        return " ".join(self.tok.batch_decode(out, skip_special_tokens=True))


class DeepLTranslator:
    def __init__(self, key):
        import requests
        self.requests = requests
        self.key = key
        self.url = "https://api-free.deepl.com/v2/translate" if key.endswith(":fx") else "https://api.deepl.com/v2/translate"
        print("[çeviri] DeepL kullanılıyor.")

    def translate(self, text):
        r = self.requests.post(
            self.url,
            headers={"Authorization": f"DeepL-Auth-Key {self.key}"},
            data={"text": text, "source_lang": "EN", "target_lang": "TR"},
            timeout=5,
        )
        r.raise_for_status()
        return r.json()["translations"][0]["text"]


AI_PROMPT = (
    "You translate English video game dialogue and subtitles into natural, fluent Turkish.\n"
    "Rules:\n"
    "- Output ONLY the Turkish translation, nothing else.\n"
    "- Sound like a professional game localization: natural spoken Turkish, keep the tone "
    "(casual, rude, formal, funny) of the original.\n"
    "- Keep character names, place names and proper nouns as they are.\n"
    "- The text comes from OCR and may contain small errors; silently fix obvious ones.\n"
    "- If a speaker name appears at the start (e.g. 'ARTHUR: ...'), keep it.\n"
    "- Previous lines are given only as context; translate only the NEW line."
)

AI_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.5-flash",
}


class AITranslator:
    """Yapay zeka API'si ile çeviri (Claude / GPT / Gemini). Son birkaç replik bağlam olarak gönderilir."""

    def __init__(self, provider, key, model=None, timeout=4.0):
        import requests
        if provider not in AI_DEFAULT_MODELS:
            raise ValueError(f"ai_provider şunlardan biri olmalı: {', '.join(AI_DEFAULT_MODELS)}")
        if not key:
            raise ValueError("ai_key boş. config.json'a API anahtarını yazın.")
        self.requests = requests
        self.session = requests.Session()
        self.provider = provider
        self.key = key
        self.model = model or AI_DEFAULT_MODELS[provider]
        self.timeout = timeout
        self.context = deque(maxlen=4)
        print(f"[çeviri] yapay zeka: {provider} / {self.model}")

    def _user_message(self, text):
        msg = ""
        if self.context:
            msg += "Previous lines (context only):\n"
            msg += "\n".join(f"EN: {en}\nTR: {tr}" for en, tr in self.context)
            msg += "\n\n"
        msg += f"NEW line to translate:\n{text}"
        return msg

    def translate(self, text):
        user = self._user_message(text)
        p, s, t = self.provider, self.session, self.timeout
        if p == "anthropic":
            r = s.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 400, "temperature": 0.3, "system": AI_PROMPT,
                      "messages": [{"role": "user", "content": user}]},
                timeout=t,
            )
            r.raise_for_status()
            out = "".join(b.get("text", "") for b in r.json()["content"])
        elif p == "openai":
            r = s.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "temperature": 0.3, "max_tokens": 400,
                      "messages": [{"role": "system", "content": AI_PROMPT}, {"role": "user", "content": user}]},
                timeout=t,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"]
        else:  # gemini
            body = {
                "system_instruction": {"parts": [{"text": AI_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400,
                                     "thinkingConfig": {"thinkingBudget": 0}},
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            r = s.post(url, params={"key": self.key}, json=body, timeout=t)
            if r.status_code == 400 and "thinking" in r.text.lower():
                body["generationConfig"].pop("thinkingConfig")
                r = s.post(url, params={"key": self.key}, json=body, timeout=t)
            r.raise_for_status()
            out = "".join(pt.get("text", "") for pt in r.json()["candidates"][0]["content"]["parts"])
        out = out.strip().removeprefix("TR:").strip()
        if not out:
            raise RuntimeError("yapay zeka boş cevap verdi")
        self.context.append((text, out))
        return out


class FallbackTranslator:
    """Önce ana çevirmeni dener; hata/zaman aşımı olursa yedeğe (yerel model) geçer."""

    def __init__(self, primary, backup):
        self.primary, self.backup = primary, backup
        self.fail_until = 0

    def translate(self, text):
        if time.time() >= self.fail_until:
            try:
                return self.primary.translate(text)
            except Exception as e:
                print(f"[çeviri] API hatası ({type(e).__name__}: {str(e)[:120]}) -> yerel model kullanılıyor")
                self.fail_until = time.time() + 20  # 20 sn boyunca API'yi deneme
        return self.backup.translate(text)


class GlossaryTranslator:
    """Oyuna özel sözlük. Zaman maliyeti yok denecek kadar az (sadece metin arama).

    sozluk/<oyun>.json:
      "names":   konuşmacı adları      {"Fisk Thug": "Fisk Haydudu"}  (listede yoksa ad olduğu gibi kalır)
      "lines":   tam satır karşılıkları {"Access Computer": "Bilgisayara eriş"}
      "fix_tr":  çeviri sonrası düzeltme {"Örümcek Adam": "Spider-Man"}
    """

    SPEAKER = re.compile(r"^\s*([A-Z][\w.'\- ]{1,30}?)\s*:\s*(.+)$")

    def __init__(self, inner, path):
        self.inner = inner
        data = {}
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            print(f"[sözlük] yüklendi: {Path(path).name}")
        elif path:
            print(f"[sözlük] bulunamadı: {path} (sözlüksüz devam)")
        key = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
        self.names = {key(k): v for k, v in data.get("names", {}).items()}
        self.lines = {key(k): v for k, v in data.get("lines", {}).items()}
        self.fix = data.get("fix_tr", {})
        self.key = key

    def translate(self, text):
        hit = self.lines.get(self.key(text))
        if hit:
            return hit
        speaker, body = None, text
        m = self.SPEAKER.match(text)
        if m and len(m.group(1).split()) <= 4 and all(w[:1].isupper() for w in m.group(1).split()):
            speaker, body = m.group(1).strip(), m.group(2)
            speaker = self.names.get(self.key(speaker), speaker)
            hit = self.lines.get(self.key(body))
        tr = hit or self.inner.translate(body)
        for wrong, right in self.fix.items():
            tr = tr.replace(wrong, right)
        return f"{speaker}: {tr}" if speaker else tr


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p] or [text]


def normalize(text):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", text.lower())).strip()


def similar(a, b):
    if not a or not b:
        return a == b
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


# --------------------------------------------------------------------------- #
# Paylaşılan durum (telefon sayfası ve TV ekranı buradan okur)
# --------------------------------------------------------------------------- #
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.id = 0
        self.en = ""
        self.tr = ""
        self.history = deque(maxlen=8)
        self.text_top = None      # İngilizce yazının üst kenarı (kaynak görüntü pikseli)
        self.mask_rect = None     # ekranda Türkçe altyazının kapladığı alan (x, y, w, h), OCR'dan gizlenir

    def publish(self, en, tr, text_top=None):
        with self.lock:
            self.text_top = text_top
            if self.tr:
                self.history.appendleft({"en": self.en, "tr": self.tr})
            self.id += 1
            self.en, self.tr = en, tr

    def clear(self):
        with self.lock:
            if self.tr:
                self.history.appendleft({"en": self.en, "tr": self.tr})
                self.id += 1
                self.en = self.tr = ""

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "en": self.en, "tr": self.tr, "history": list(self.history)}


# --------------------------------------------------------------------------- #
# İşleme döngüsü
# --------------------------------------------------------------------------- #
class Pipeline(threading.Thread):
    def __init__(self, source, roi, ocr, translator, state, interval=0.2, keep_seconds=0.7, verbose=True):
        super().__init__(daemon=True)
        self.source, self.roi, self.ocr, self.tr = source, roi, ocr, translator
        self.state = state
        self.interval = interval
        self.keep_seconds = keep_seconds
        self.verbose = verbose
        self.cache = {}
        self.running = True

    def run(self):
        prev_small = None
        candidate = ""        # son OCR sonucu (henüz onaylanmamış)
        shown = ""            # ekranda gösterilen metnin normal hali
        empty_since = None
        while self.running:
            t_start = time.time()
            img = self.source.grab(self.roi)
            if img is None:
                time.sleep(0.1)
                continue
            self._mask_overlay(img)

            # Görüntü değişmediyse OCR yapma
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (256, max(8, int(256 * gray.shape[0] / gray.shape[1]))))
            changed = prev_small is None or float(np.mean(cv2.absdiff(small, prev_small))) > 1.0
            prev_small = small
            if not changed and candidate == shown and empty_since is None:
                self._sleep(t_start)
                continue

            t0 = time.time()
            text = self.ocr.read(img)
            ocr_ms = (time.time() - t0) * 1000
            norm = normalize(text)

            # Güvenlik: kendi Türkçe altyazımızı okuduysak "yazı yok" say
            cur_tr = normalize(self.state.tr)
            if cur_tr and len(norm) >= 3 and (similar(norm, cur_tr) or norm in cur_tr or cur_tr in norm):
                norm = ""

            if len(norm) < 3:
                # Yazı yok: kısa bir süre bekle, sonra temizle
                empty_since = empty_since or time.time()
                if (shown or self.state.tr) and time.time() - empty_since > self.keep_seconds:
                    self.state.clear()
                    shown = ""
                candidate = ""
                self._sleep(t_start)
                continue
            empty_since = None

            if similar(norm, shown):
                candidate = shown
            elif similar(norm, candidate):
                # Aynı metin üst üste iki kez okundu -> yazı oturdu, çevir
                t1 = time.time()
                tr = self.cache.get(norm)
                if tr is None:
                    try:
                        tr = self.tr.translate(text)
                    except Exception as e:  # ağ hatası vb.
                        print(f"[çeviri] hata: {e}")
                        self._sleep(t_start)
                        continue
                    self.cache[norm] = tr
                tr_ms = (time.time() - t1) * 1000
                top = self.ocr.last_top
                self.state.publish(text, tr, None if top is None else self.roi[1] + top)
                shown = candidate = norm
                if self.verbose:
                    print(f"[{ocr_ms:4.0f}ms OCR | {tr_ms:4.0f}ms çeviri] {text}\n    -> {tr}")
            else:
                candidate = norm
            self._sleep(t_start)

    def _mask_overlay(self, img):
        """Türkçe altyazımız okunan alanın üstüne biniyorsa o kısmı karart (OCR kendi yazımızı okumasın)."""
        m = self.state.mask_rect
        if not m or not self.state.tr:
            return
        rx, ry = self.roi[0], self.roi[1]
        x0, y0 = max(0, int(m[0] - rx)), max(0, int(m[1] - ry))
        x1, y1 = min(img.shape[1], int(m[0] + m[2] - rx)), min(img.shape[0], int(m[1] + m[3] - ry))
        if x1 > x0 and y1 > y0:
            img[y0:y1, x0:x1] = 0

    def _sleep(self, t_start):
        rest = self.interval - (time.time() - t_start)
        if rest > 0:
            time.sleep(rest)


# --------------------------------------------------------------------------- #
# Telefon sayfası (yerel ağ)
# --------------------------------------------------------------------------- #
PHONE_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PS5 Çeviri</title>
<style>
 body{margin:0;background:#0b0d10;color:#f2f2f2;font-family:system-ui,sans-serif;padding:16px}
 #tr{font-size:clamp(22px,6vw,40px);line-height:1.35;font-weight:600;min-height:3em}
 #en{color:#8a93a0;font-size:16px;margin-top:10px}
 h2{font-size:13px;color:#5d6673;text-transform:uppercase;letter-spacing:.08em;margin:28px 0 8px}
 .h{border-top:1px solid #1f242b;padding:10px 0;font-size:17px;color:#c9ced6}
 .h small{display:block;color:#6b7480;font-size:13px;margin-top:3px}
 #dot{position:fixed;top:10px;right:12px;width:10px;height:10px;border-radius:50%;background:#2ecc71}
 button{position:fixed;bottom:12px;right:12px;background:#1f242b;color:#c9ced6;border:0;padding:8px 12px;border-radius:8px}
</style></head><body>
<div id="dot"></div>
<div id="tr">Bekleniyor…</div><div id="en"></div>
<h2>Önceki</h2><div id="hist"></div>
<button onclick="document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen()">Tam ekran</button>
<script>
let last=-1;const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
 try{const r=await fetch('/api',{cache:'no-store'});const d=await r.json();
  document.getElementById('dot').style.background='#2ecc71';
  if(d.id!==last){last=d.id;
   document.getElementById('tr').textContent=d.tr||'…';
   document.getElementById('en').textContent=d.en||'';
   document.getElementById('hist').innerHTML=d.history.map(h=>`<div class="h">${esc(h.tr)}<small>${esc(h.en)}</small></div>`).join('');}
 }catch(e){document.getElementById('dot').style.background='#e74c3c';}
 setTimeout(tick,250);}
tick();
</script></body></html>"""


def start_web(state, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api"):
                body = json.dumps(state.snapshot(), ensure_ascii=False).encode("utf-8")
                ctype = "application/json; charset=utf-8"
            else:
                body = PHONE_HTML.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ip = local_ip()
    print("\n" + "=" * 60)
    print(f"  Telefonda aç:  http://{ip}:{port}")
    print("  (Telefon ve bilgisayar aynı Wi-Fi'da olmalı)")
    print("=" * 60 + "\n")
    return srv


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# TV modu: kart görüntüsünü tam ekran göster + altyazı
# --------------------------------------------------------------------------- #
GAP = 6  # Türkçe altyazı ile İngilizce yazı arasındaki boşluk (piksel)
SUB_STYLE = ""  # main() içinde config'e göre doldurulur


def make_style(cfg):
    fg = cfg.get("tv_text_color", "#111111")
    bg = cfg.get("tv_bg_color", "rgba(215,215,215,225)")
    return f"color:{fg};background:{bg};padding:6px 18px;border-radius:8px;"


def run_tv(source, state, screen_index, font_size):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QImage, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)

    class TV(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("PS5 Çeviri - TV (çıkış: ESC)")
            self.setStyleSheet("background:black;")
            self.video = QLabel(self)
            self.video.setAlignment(Qt.AlignCenter)
            self.sub = QLabel(self)
            self.sub.setWordWrap(True)
            self.sub.setAlignment(Qt.AlignCenter)
            self.sub.setFont(QFont("Segoe UI", font_size, QFont.Bold))
            self.sub.setStyleSheet(SUB_STYLE)
            self.sub.hide()
            self.last_frame_id = -1
            self.last_state_id = -1
            self.sub_on_top = False
            self.frame_size = None
            t = QTimer(self)
            t.timeout.connect(self.tick)
            t.start(8)

        def keyPressEvent(self, e):
            if e.key() == Qt.Key_Escape:
                self.close()
            elif e.key() == Qt.Key_T:   # altyazı üstte / altta
                self.sub_on_top = not self.sub_on_top
                self.place_sub()

        def resizeEvent(self, e):
            self.video.setGeometry(0, 0, self.width(), self.height())
            self.place_sub()

        def place_sub(self):
            w = int(self.width() * 0.8)
            self.sub.setFixedWidth(w)
            self.sub.adjustSize()
            x = (self.width() - w) // 2
            if self.sub_on_top:
                y = 40
            elif state.text_top is not None and self.frame_size:
                # İngilizce yazının hemen üstüne (kare -> pencere koordinatı)
                fw, fh = self.frame_size
                k = min(self.width() / fw, self.height() / fh)
                oy = (self.height() - fh * k) / 2
                y = int(oy + state.text_top * k - self.sub.height() - GAP)
                y = max(10, y)
            else:
                y = self.height() - self.sub.height() - 40
            self.sub.move(x, y)

        def tick(self):
            frame, fid = source.latest()
            if frame is not None and fid != self.last_frame_id:
                self.last_frame_id = fid
                h, w = frame.shape[:2]
                self.frame_size = (w, h)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
                pm = QPixmap.fromImage(img)
                if (w, h) != (self.width(), self.height()):
                    pm = pm.scaled(self.width(), self.height(), Qt.KeepAspectRatio, Qt.FastTransformation)
                self.video.setPixmap(pm)
            snap_id = state.id
            if snap_id != self.last_state_id:
                self.last_state_id = snap_id
                s = state.snapshot()
                if s["tr"]:
                    self.sub.setText(s["tr"])
                    self.sub.show()
                    self.place_sub()
                    self.sub.raise_()
                else:
                    self.sub.hide()

    win = TV()
    screens = app.screens()
    scr = screens[min(screen_index, len(screens) - 1)]
    win.setGeometry(scr.geometry())
    win.showFullScreen()
    print(f"[tv] ekran {screen_index}: {scr.name()}  (ESC = çıkış, T = altyazı üst/alt)")
    app.exec()


# --------------------------------------------------------------------------- #
# Remote Play TV modu: oyunun üstünde şeffaf altyazı katmanı
# --------------------------------------------------------------------------- #
def run_overlay(state, mon, font_size, roi):
    import signal
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)

    # Yakalanan monitörle aynı ekranı bul
    scr = None
    for s in app.screens():
        g, r = s.geometry(), s.devicePixelRatio()
        if abs(g.x() * r - mon["left"]) < 8 and abs(g.y() * r - mon["top"]) < 8:
            scr = s
            break
    scr = scr or app.primaryScreen()

    class Overlay(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                | Qt.WindowTransparentForInput)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setGeometry(scr.geometry())
            self.sub = QLabel(self)
            self.sub.setWordWrap(True)
            self.sub.setAlignment(Qt.AlignCenter)
            self.sub.setFont(QFont("Segoe UI", font_size, QFont.Bold))
            self.sub.setStyleSheet(SUB_STYLE)
            self.sub.hide()
            self.last_id = -1
            self.ticks = 0
            t = QTimer(self)
            t.timeout.connect(self.tick)
            t.start(30)

        def place_sub(self):
            w = int(self.width() * 0.8)
            self.sub.setFixedWidth(w)
            self.sub.adjustSize()
            # Türkçe altyazıyı İngilizce yazının hemen üstüne koy (yazı konumu bilinmiyorsa alanın üstüne).
            # Kapladığı yer OCR'dan gizlenir, böylece program kendi yazısını okumaz.
            r = scr.devicePixelRatio()
            anchor = state.text_top if state.text_top is not None else roi[1]
            y = int(anchor / r - self.sub.height() - GAP)
            if y < 10:
                y = int((roi[1] + roi[3]) / r + GAP)
            y = max(10, min(y, self.height() - self.sub.height() - 10))
            x = (self.width() - w) // 2
            state.mask_rect = (x * r, y * r, w * r, self.sub.height() * r)
            self.sub.move(x, y)

        def tick(self):
            self.ticks += 1
            if self.ticks % 66 == 0:   # ~2 sn'de bir en üste getir (tam ekran pencereler örtmesin)
                self.raise_()
            if state.id != self.last_id:
                self.last_id = state.id
                s = state.snapshot()
                if s["tr"]:
                    self.sub.setText(s["tr"])
                    self.place_sub()
                    self.sub.show()
                else:
                    self.sub.hide()

    win = Overlay()
    win.show()
    if sys.platform == "win32":
        # Katmanı ekran yakalamadan gizle -> OCR kendi Türkçe altyazımızı okumasın
        try:
            import ctypes
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(int(win.winId()), 0x11)  # WDA_EXCLUDEFROMCAPTURE
            if not ok:
                print("[tv] not: katman ekran yakalamadan gizlenemedi; altyazı okunan alanın dışına konuyor.")
        except Exception as e:
            print(f"[tv] yakalama hariç tutma ayarlanamadı: {e}")

    # Ctrl+C ile kapatılabilsin
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)

    print(f"[tv] altyazı katmanı açık: {scr.name()}  (kapatmak için bu pencerede Ctrl+C)")
    app.exec()


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="PS5 İngilizce -> Türkçe ekran çevirisi")
    p.add_argument("--source", choices=["card", "screen", "image"], default="card")
    p.add_argument("--device", type=int, default=None, help="capture kartı numarası (genelde 0 veya 1)")
    p.add_argument("--monitor", type=int, default=None, help="ekran kaynağı için monitör numarası (1, 2, ...)")
    p.add_argument("--image", help="test için resim dosyası")
    p.add_argument("--mode", choices=["phone", "tv"], default="phone")
    p.add_argument("--screen", type=int, default=None, help="TV modunda kullanılacak ekran (0, 1, ...)")
    p.add_argument("--font", type=int, default=None, help="TV altyazı yazı boyutu")
    p.add_argument("--engine", choices=["local", "deepl", "ai"], default=None)
    p.add_argument("--deepl-key", default=os.environ.get("DEEPL_KEY"))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--select-roi", action="store_true", help="altyazı alanını yeniden seç")
    p.add_argument("--game", default=None, help="oyun sözlüğü adı (sozluk/<ad>.json)")
    p.add_argument("--once", action="store_true", help="test: tek seferlik OCR + çeviri yap ve çık")
    args = p.parse_args()

    cfg = load_config()
    device = args.device if args.device is not None else cfg.get("device", 0)
    engine = args.engine or cfg.get("engine", "local")
    deepl_key = args.deepl_key or cfg.get("deepl_key")
    screen_index = args.screen if args.screen is not None else cfg.get("tv_screen", 1)
    font_size = args.font or cfg.get("tv_font", 22)
    global SUB_STYLE
    SUB_STYLE = make_style(cfg)

    if args.source == "card":
        source = CardSource(device)
    elif args.source == "screen":
        source = ScreenSource(args.monitor if args.monitor is not None else cfg.get("monitor", 1))
    else:
        if not args.image:
            p.error("--source image için --image dosya.png verin")
        source = ImageSource(args.image)

    roi_key = f"roi_{args.source}"
    roi = cfg.get(roi_key)
    if args.select_roi or not roi:
        if args.once and not roi:
            f = source.full_frame()
            roi = [0, 0, f.shape[1], f.shape[0]]
        else:
            if args.source == "screen":
                print("\n>>> 5 saniye içinde Remote Play penceresine geç ve TAM EKRAN yap.")
                print(">>> Ekranda İngilizce altyazı görünüyor olsun.\n")
                for i in range(5, 0, -1):
                    print(f"   {i}...", flush=True)
                    time.sleep(1)
            print("Altyazı / diyalog kutusunun olduğu alanı seçin...")
            roi = select_roi(source.full_frame())
            if not roi:
                print("Alan seçilmedi, çıkılıyor.")
                return
            cfg[roi_key] = roi
            save_config(cfg)
            print(f"Alan kaydedildi: {roi}")

    print("[ocr] yükleniyor...")
    ocr = OCR(min_score=float(cfg.get("ocr_min_score", 0.75)), use_gpu=cfg.get("ocr_gpu", True))
    if engine == "deepl":
        if not deepl_key:
            p.error("DeepL için --deepl-key verin ya da config.json'a deepl_key yazın")
        translator = DeepLTranslator(deepl_key)
    elif engine == "ai":
        ai = AITranslator(cfg.get("ai_provider"), cfg.get("ai_key"), cfg.get("ai_model"),
                          timeout=float(cfg.get("ai_timeout", 4)))
        if cfg.get("ai_fallback_local", True):
            translator = FallbackTranslator(ai, make_local_translator(cfg))
        else:
            translator = ai
    else:
        translator = make_local_translator(cfg)
    game = args.game or cfg.get("game")
    translator = GlossaryTranslator(translator, BASE_DIR / "sozluk" / f"{game}.json" if game else None)

    if args.once:
        img = source.grab(roi)
        t0 = time.time(); text = ocr.read(img); t1 = time.time()
        tr = translator.translate(text) if text else ""
        t2 = time.time()
        print(f"OCR ({(t1-t0)*1000:.0f} ms): {text}")
        print(f"TR  ({(t2-t1)*1000:.0f} ms): {tr}")
        return

    # Isınma: ilk OCR (özellikle GPU'da) yavaştır, oyun başlamadan yapılsın
    try:
        warm = source.grab(roi)
        if warm is not None:
            t0 = time.time(); ocr.read(warm); ocr.read(warm)
            print(f"[ocr] ısınma tamam ({(time.time()-t0)*500:.0f} ms/okuma)")
    except Exception:
        pass

    state = State()
    start_web(state, args.port)
    pipe = Pipeline(source, roi, ocr, translator, state, keep_seconds=float(cfg.get("keep_seconds", 0.7)))
    pipe.start()

    try:
        if args.mode == "tv":
            if args.source == "screen":
                run_overlay(state, source.mon, font_size, roi)
            elif args.source == "card":
                run_tv(source, state, screen_index, font_size)
            else:
                while True:
                    time.sleep(1)
        else:
            print("Çalışıyor. Durdurmak için bu pencerede Ctrl+C.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pipe.running = False
        source.close()


if __name__ == "__main__":
    main()
