# PS5 Çeviri — Kurulum ve Kullanım

Ekrandaki İngilizce yazıyı okuyup Türkçeye çevirir. Her şey bilgisayarında çalışır: görüntü internete gitmez, çeviri internetsiz yapılır (RTX 3050 ekran kartı kullanılır).

## 1. Bir kerelik kurulum

Anaconda Prompt'u aç, bu klasöre gel ve sırayla çalıştır:

```bat
conda create -n ps5ceviri python=3.11 -y
conda activate ps5ceviri
pip install torch --index-url https://download.pytorch.org/whl/cu126
pip install -r requirements.txt
python -c "import app; app.LocalTranslator(); import torch; print('GPU:', torch.cuda.is_available())"
```

Son komut çeviri modelini (~1 GB) indirir ve sonunda `GPU: True` yazmalı.

`.bat` dosyalarına çift tıklayınca çalışması için bir kere `conda init cmd.exe` çalıştır. Ortama farklı bir isim verdiysen `.bat` dosyalarındaki `set ENV=ps5ceviri` satırını değiştir.

## Hızlandırma (önerilir)

OCR'ı ekran kartına taşır ve çeviriyi ~3 kat hızlandırır. Anaconda Prompt'ta:

```bat
conda activate ps5ceviri
pip uninstall -y onnxruntime
pip install onnxruntime-gpu==1.23.2 ctranslate2==4.8.5
```

- `onnxruntime-gpu` sürümü **1.23.2** olmalı: daha yenileri CUDA 13 istiyor, bizim torch CUDA 12.
- İlk açılışta çeviri modeli bir kereliğine hızlı formata dönüştürülür (`models/` klasörü, ~1 dk).
- Siyah pencerede `[ocr] hazır (GPU)` ve `[çeviri] hızlı model yükleniyor (cuda, float16)` görmelisin.
- Bir sorun olursa program kendiliğinden eski (yavaş ama çalışan) yönteme döner.
- Sonradan `pip install -r requirements.txt` çalıştırırsan `onnxruntime` geri gelebilir; o zaman yukarıdaki iki komutu tekrarla.
- Kapatmak için `config.json`: `"ocr_gpu": false` ve/veya `"fast_translate": false`.

## 2. PS5 ayarları

- *Ayarlar → Sistem → HDMI* → **HDCP'yi Etkinleştir: Kapalı** (yoksa kart görüntü alamaz)
- *Ayarlar → Ekran ve Video → Video Çıkışı* → **Çözünürlük: 1080p**

## 3. Bağlantı

**Seçenek A — Çeviri telefonda (oyunda sıfır gecikme)** → `1_kart_telefon.bat`

```
PS5 ──HDMI──> Kart [HDMI INPUT]
              Kart [HDMI OUTPUT] ──HDMI──> TV
              Kart [USB 3.0] ──USB──> PC (mavi USB girişine)
```

**Seçenek B — Altyazı TV'de, görüntünün üstünde** → `2_kart_tv.bat`

```
PS5 ──HDMI──> Kart [HDMI INPUT]
              Kart [USB 3.0] ──USB──> PC
PC ──HDMI──> TV   (Windows'ta ekranı "Genişlet" yap, TV 2. ekran olsun)
```
- Ses için kulaklığı kartın kulaklık girişine tak, ya da Windows'ta:
  *Ses ayarları → Diğer ses ayarları → Kayıt → (kart) → Özellikler → Dinle → "Bu cihazı dinle"* → çıkış olarak TV'yi seç.
- Pencere TV'de açılmazsa `config.json` içindeki `"tv_screen"` değerini 0 ya da 1 yap.
- **ESC** = çıkış, **T** = altyazıyı üste/alta al.
- Bu modda yaklaşık 50–100 ms görüntü gecikmesi olur. Hikâye oyunlarında hissedilmez.

**Kart gelene kadar — Remote Play ile TV'de** → `4_remoteplay_tv.bat`

```
PS5 ──(ev ağı)──> PC'de Remote Play ──HDMI──> TV  (Türkçe altyazı üstte)
```
1. PC'yi HDMI ile TV'ye bağla. Windows'ta **Win + P** → **"Yalnızca ikinci ekran"** seç (görüntü sadece TV'ye gider).
2. PS Remote Play'i aç, PS5'e bağlan, **tam ekran** yap. Kumandayı PC'ye USB ile bağla.
3. `4_remoteplay_tv.bat` dosyasını çalıştır. İlk açılışta altyazı alanını seç, sonra oyuna dön.
4. Kapatmak için siyah pencerede **Ctrl+C**.

TV'yi "Genişlet" modunda 2. ekran olarak kullanacaksan `config.json`'a `"monitor": 2` yaz.
(Çeviriyi telefonda görmek istersen: `3_remoteplay_telefon.bat`.)

## 4. İlk çalıştırma: altyazı alanını seçme

İlk açılışta oyunun bir karesi gösterilir. Fareyle **altyazının ya da diyalog kutusunun çıktığı alanı** dikdörtgen içine al ve **ENTER**'a bas. Seçim kaydedilir.
Alanı değiştirmek için `alan_sec.bat` dosyasını aç.

İpucu: Alanı biraz geniş tut (2–3 satır sığsın), ama tüm ekranı seçme. Küçük alan = daha hızlı ve daha doğru.

## 5. Telefonda açma

Program açılınca siyah pencerede şuna benzer bir adres yazar:

```
Telefonda aç:  http://192.168.1.x:x
```

Telefon aynı Wi-Fi'dayken bu adresi tarayıcıda aç. Altta "Önceki" kısmında son cümleler de durur.
Açılmazsa Windows "erişime izin ver" diye sorduğunda **Özel ağlar**'a izin ver.

## Ayarlar (`config.json`)

Program ilk çalıştığında oluşur. Elle değiştirebileceğin alanlar:

| Ayar | Anlamı | Varsayılan |
|---|---|---|
| `device` | Kart numarası. Görüntü gelmezse 1 yap | 0 |
| `tv_screen` | TV modunda hangi ekran | 1 |
| `tv_font` | Türkçe altyazı yazı boyutu | 22 |
| `tv_bg_color` | Altyazı arka planı (açık gri, yarı saydam) | `rgba(215,215,215,225)` |
| `tv_text_color` | Altyazı yazı rengi | `#111111` |
| `keep_seconds` | Yazı kaybolunca Türkçenin kalkma süresi (sn) | 0.7 |
| `ocr_min_score` | OCR güven eşiği; anlamsız okumalar çoksa artır (0.8) | 0.75 |
| `engine` | `local` (internetsiz) veya `deepl` | local |
| `deepl_key` | DeepL kullanacaksan API anahtarı | — |

Örnek:
```json
{ "device": 0, "tv_screen": 1, "tv_font": 34, "engine": "local" }
```

**DeepL (isteğe bağlı, daha doğal çeviri):** deepl.com'da ücretsiz "DeepL API Free" hesabı aç (ayda 500.000 karakter). Anahtarı `deepl_key` alanına yaz, `engine` değerini `"deepl"` yap. Bu durumda internete sadece metin gider, görüntü gitmez.

### Yapay zeka ile çeviri (şu an KAPALI — isteğe bağlı)

Varsayılan olarak yerel model kullanılır. Daha doğal çeviri istersen Claude, GPT veya Gemini API'si kullanılabilir. Son 4 replik bağlam olarak gönderilir, karakter isimleri çevrilmez. API cevap vermezse otomatik olarak yerel modele geçer.

Açmak için `config.json`:
```json
{
  "engine": "ai",
  "ai_provider": "anthropic",
  "ai_key": "API-ANAHTARIN",
  "ai_model": "",
  "ai_timeout": 4,
  "ai_fallback_local": true
}
```
- `ai_provider`: `anthropic` (Claude), `openai` (GPT) veya `gemini`
- `ai_model` boş bırakılırsa ucuz ve hızlı varsayılan model kullanılır: `claude-haiku-4-5`, `gpt-4.1-mini`, `gemini-2.5-flash`. Model adı ileride değişirse buraya yenisini yaz.
- Kapatmak için `"engine": "local"` yap.

### Oyuna özel sözlük

`sozluk` klasöründe her oyun için bir dosya olur (örnek: `sozluk/spiderman.json`). Hangi oyunun kullanılacağını `config.json`'da `"game": "spiderman"` ile seçersin. Zaman maliyeti yoktur.

```json
{
  "names":  { "Fisk Thug": "Fisk'in Adamı" },
  "lines":  { "Access Computer": "Bilgisayara eriş" },
  "fix_tr": { "Örümcek Adam": "Spider-Man" }
}
```
- `names`: Konuşmacı adı ("İsim: replik"). Ad hiç çevrilmez. Listede varsa senin yazdığın karşılık kullanılır.
- `lines`: Bu satır görülürse modele hiç sorulmadan senin yazdığın kullanılır.
- `fix_tr`: Çeviride geçen yanlış ifade senin yazdığınla değiştirilir.

Yeni oyun için dosyayı kopyala, adını değiştir (ör. `rdr2.json`) ve `config.json`'da `"game": "rdr2"` yap.

## Sorun giderme

| Sorun | Çözüm |
|---|---|
| "Capture kartı açılamadı" | OBS gibi kartı kullanan programları kapat. `device` değerini 1 yap. |
| Siyah görüntü / "görüntü gelmiyor" | PS5'te HDCP kapalı mı? Kart USB 3.0 (mavi) girişte mi? |
| Yazıyı okumuyor | Alanı yeniden seç (`alan_sec.bat`). Oyunda altyazı boyutunu büyütmek çok işe yarar. |
| Çeviri yavaş | Siyah pencerede `OCR | çeviri` süreleri yazar. `GPU: False` çıkıyorsa torch CUDA olmadan kurulmuştur, torch komutunu tekrar çalıştır. |
| Telefonda sayfa açılmıyor | Aynı Wi-Fi mı? Windows Güvenlik Duvarı'nda Python'a izin ver. |

## Nasıl çalışır?

1. Seçilen alan saniyede ~5 kez kontrol edilir. Görüntü değişmediyse hiçbir şey yapılmaz.
2. Değiştiyse OCR (RapidOCR) yazıyı okur.
3. Aynı yazı üst üste iki kez okunursa (yazı ekrana oturmuş demektir) çevrilir. Böylece harf harf beliren yazılar yarım çevrilmez.
4. Çeviri `Helsinki-NLP/opus-mt-tc-big-en-tr` modeliyle ekran kartında yapılır. Aynı cümle tekrar gelirse hafızadan anında gösterilir.
5. Yazı kaybolunca 0,7 saniye sonra çeviri de kalkar (`config.json` → `"keep_seconds"` ile değiştirilebilir, ör. 0.5).
