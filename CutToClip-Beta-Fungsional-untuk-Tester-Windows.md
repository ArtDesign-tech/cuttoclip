# CutToClip Beta Fungsional untuk Tester Windows

## Ringkasan

- Ganti installer simulasi menjadi `CutToClip Beta` versi `0.2.0-beta.1`, identifier `app.cuttoclip.studio.beta`, dengan penyimpanan terpisah dari aplikasi utama.
- Beta menjalankan pipeline asli: video lokal/YouTube → transkripsi → AI Moments → preview akurat → render MP4.
- Sediakan dua mode provider:
  - `Managed Beta`: tester memasukkan kode undangan sekali.
  - `API Key Saya`: Groq untuk transkripsi dan Gemini dari Google AI Studio untuk AI Moments.
- Installer kecil; worker, FFmpeg, FFprobe, Deno, model YuNet/Silero, dan font diunduh dari GitHub Release saat first launch.
- Mode simulasi tetap hanya untuk pengembangan UI dan tidak masuk build Beta.

## Onboarding dan Provider

- First launch menampilkan alur: pilih provider → masukkan invite atau dua API key → persetujuan privasi → unduh runtime → mulai aplikasi.
- Mode BYOK menggunakan:
  - Groq `whisper-large-v3-turbo`, endpoint transkripsi dengan word/segment timestamps. [Dokumentasi Groq](https://console.groq.com/docs/speech-to-text)
  - Gemini Interactions API v1, model stabil `gemini-3.5-flash`, structured output, dan `store=false`. [Gemini 3.5 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash), [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)
- Port algoritma windowing 12 menit, overlap 30 detik, validasi durasi/timestamp, ranking, dan deduplikasi AI Moments dari gateway ke adapter Gemini lokal.
- Tidak ada fallback diam-diam antar-provider. Kegagalan key menampilkan tindakan `Perbarui API key` atau `Beralih ke Managed Beta`.
- Simpan installation token dan API key dalam vault Stronghold yang dibuka memakai kunci acak terlindungi Windows DPAPI. Secret tidak masuk JSON, localStorage, log, command line, atau installer.
- Settings menyediakan pemilihan mode, status provider, validasi key, penghapusan key, reaktivasi invite, serta ekspor diagnosis yang tidak menyertakan media, transcript, atau secret.

## Runtime dan Desktop Lifecycle

- Buat entrypoint worker khusus yang benar-benar menjalankan Uvicorn; paketkan sebagai PyInstaller `onedir` agar dependensi native dan font stabil. PyInstaller mendukung data bundle yang tetap dapat ditemukan melalui `__file__`. [Dokumentasi PyInstaller](https://pyinstaller.org/en/stable/runtime-information.html)
- Runtime ZIP berisi worker Python, FFmpeg/FFprobe, yt-dlp, Deno, YuNet, Silero VAD, font, dan third-party notices. Semua versi, URL sumber, lisensi, dan SHA-256 dikunci dalam `runtime-lock.json`.
- Urutan release:
  1. Bangun `CutToClip-Runtime-v0.2.0-beta.1-windows-x64.zip`.
  2. Unggah ke GitHub Release.
  3. Masukkan URL HTTPS dan SHA-256 final ke konfigurasi Beta.
  4. Bangun installer.
- Downloader Rust mendukung progress, cancel, retry/HTTP Range, verifikasi SHA-256, penolakan path traversal ZIP, ekstraksi atomik, dan pembersihan file korup/parsial.
- Runtime dipasang di `%LOCALAPPDATA%\CutToClip Beta\runtime\<version>`. Data project berada di direktori terpisah dan output tetap di `Videos\CutToClip Beta`.
- Tauri memilih port loopback kosong, menjalankan worker tersembunyi, menunggu health check, menangkap log teredaksi, dan mematikan worker melalui Windows Job Object saat aplikasi selesai.
- Frontend memperoleh base URL worker dari `bootstrap_status` sebelum memanggil API. Tambahkan origin produksi Tauri 2 `http://tauri.localhost` ke CORS worker. [Tauri Windows origin](https://v2.tauri.app/start/migrate/from-tauri-1/)
- Tambahkan CSP terbatas untuk IPC dan worker loopback; build Beta tidak memakai `VITE_DEMO_MODE`.

## API, Gateway, dan Keamanan

- Tambahkan tipe frontend/Rust:
  - `ProviderMode = managed | byok`
  - `ProviderStatus`
  - `BootstrapState`
  - `RuntimeInstallState`
- Tambahkan command Tauri:
  - `bootstrap_status`
  - `install_runtime` / `cancel_runtime_install`
  - `activate_managed`
  - `save_byok_credentials` / `clear_byok_credentials`
  - `set_provider_mode`
  - `restart_worker`
- Tambahkan konfigurasi worker `CUTTOCLIP_PROVIDER_MODE`, key Groq/Gemini, model provider, port, serta lokasi runtime/data/output. `SystemCapabilities` melaporkan mode dan model tanpa membocorkan key.
- Gateway mempertahankan invite satu kali dan installation bearer token, serta menambah `GET /v1/me` untuk identitas instalasi dan sisa kuota.
- Default Managed Beta:
  - maksimal 120 menit transkripsi per instalasi per hari;
  - maksimal 30 highlight windows per hari;
  - reservasi kuota dilakukan atomik di SQLite dan dikembalikan jika upstream gagal;
  - pelanggaran mengembalikan `429 daily_quota_exceeded`.
- Jangan bundel Cloudflare Access client secret. Service token Cloudflare merupakan pasangan Client ID/Secret yang harus disimpan sebagai credential, bukan disebarkan dalam aplikasi. [Cloudflare service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)
- Publikasikan API beta melalui Tunnel dengan installation token, invite berentropi tinggi, WAF/rate limit, provider spending cap, dan admin CLI yang tetap lokal.
- Berpindah provider memerlukan worker restart dan dinonaktifkan saat ada job aktif.

## Pengujian dan Artefak

- Uji unit adapter Groq/Gemini, structured output, retry/429, invalid key, revoked invite, kuota, dan jaminan BYOK tidak menghubungi gateway.
- Uji downloader untuk resume, cancel, checksum salah, ZIP traversal, ruang disk habis, runtime rusak, dan pemasangan atomik.
- Uji supervisor untuk dynamic port, startup timeout, crash/restart terbatas, shutdown bersih, dan tidak meninggalkan worker.
- Jalankan E2E pada Windows bersih tanpa Python, Node, FFmpeg, atau Deno:
  - Managed invite → runtime download → video lokal/YouTube → AI Moments → preview → render.
  - BYOK Groq+Gemini → alur yang sama tanpa trafik AI ke gateway.
  - Cleanup source YouTube → restore → preview dan render pulih.
- Jalankan seluruh web/worker/gateway tests, Playwright, production build, Cargo check, secret scan, license audit, installer install/upgrade/uninstall smoke test.
- Uninstaller menghapus aplikasi, runtime, dan log, tetapi mempertahankan project serta hasil render.
- Artefak:
  - `CutToClip-Runtime-v0.2.0-beta.1-windows-x64.zip` + SHA-256.
  - `CutToClip-Beta-v0.2.0-beta.1-x64-setup.exe` + SHA-256.
- Beta pertama tetap unsigned; dokumentasikan kemungkinan peringatan Windows SmartScreen.
