# CutToClip Beta dan Model Akses Publik

## Model Akses

| Build | Akses Beta milik Anda | API key pengguna |
|---|---:|---:|
| `CutToClip Beta` | Tersedia dengan kode undangan | Tersedia |
| `CutToClip` publik | Tidak ditampilkan | Default dan tanpa undangan |

Kode undangan bukan lisensi aplikasi. Tester yang tidak memiliki kode tetap dapat memilih `API Key Saya`.

## Provider

- `Akses Beta`: installation token diperoleh dari invite satu kali. Groq dan AI Moments dibayar melalui gateway Anda, dengan batas 120 menit transkripsi per hari.
- `API Key Saya`: pengguna memasukkan:
  - Groq untuk transkripsi.
  - Gemini Google AI Studio untuk AI Moments.
- Key disimpan terenkripsi dengan Stronghold dan Windows DPAPI.
- Tidak ada perpindahan otomatis antar-mode agar biaya dan privasi tidak berubah tanpa persetujuan.
- Build publik menyembunyikan seluruh onboarding invite dan tidak bergantung pada gateway Anda.

## Aplikasi Beta Fungsional

- Gunakan identitas `CutToClip Beta` / `app.cuttoclip.studio.beta`.
- Hilangkan adapter simulasi dari build Beta.
- First launch mengunduh runtime asli dari GitHub Release: worker, FFmpeg/FFprobe, yt-dlp, Deno, YuNet, Silero, dan font.
- Setelah verifikasi SHA-256, Tauri menjalankan worker otomatis dan menampilkan Create Clip.
- Seluruh fitur video lokal, YouTube, AI Moments, preview akurat, cache cleanup, restore source, dan render MP4 tetap berfungsi.

## Distribusi Publik Berikutnya

- Gunakan channel build `beta` dan `public`.
- Channel `beta` menampilkan pilihan `Akses Beta` dan `API Key Saya`.
- Channel `public` langsung mengarahkan pengguna ke setup Groq + Gemini miliknya.
- Managed/cloud service hanya ditambahkan kembali pada rilis publik jika kelak tersedia paket berbayar atau kuota akun, bukan dengan kode invite tester.

## Pengujian

- Uji tester dengan invite tanpa memasukkan API key.
- Uji tester tanpa invite menggunakan Groq + Gemini sendiri.
- Pastikan token/key tidak masuk log, localStorage, installer, atau diagnostic export.
- Pastikan build publik dapat menjalankan pipeline lengkap ketika gateway Anda mati.
- Uji clean Windows tanpa Python, Node, FFmpeg, atau dependency manual.
