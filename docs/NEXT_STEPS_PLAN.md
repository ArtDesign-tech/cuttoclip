# Rencana Pengembangan CutToClip

## Ringkasan

Core pipeline CutToClip sudah tersedia: sumber video, transkripsi, analisis AI, editing, render lokal, persistensi project, job polling, cancellation, dan project library. Fokus berikutnya bukan menambah fitur produk, tetapi memastikan integritas data, menyelesaikan distribusi desktop, dan mengeraskan worker lokal sebelum closed beta berikutnya.

Urutan prioritas:

1. Stabilitas data dan recovery workflow.
2. Desktop vertical slice yang dapat dipasang dan dijalankan.
3. Keamanan dan reliability worker lokal.
4. Release engineering dan maintainability.

## Sasaran

- Tidak ada edit project yang hilang atau diterapkan ke project lain.
- Project gagal atau dibatalkan dapat dilanjutkan tanpa membuat project baru.
- Aplikasi desktop dapat menjalankan dan menghentikan worker secara otomatis.
- API worker hanya dapat digunakan oleh client lokal yang sah.
- Upload tidak dapat menghabiskan disk tanpa batas.
- Build, test, dan packaging dapat diulang melalui CI Windows.

## Di Luar Scope

- Fitur editing atau preset baru.
- Hardware encoding sebelum fallback dan determinisme output diuji.
- Refactor besar sebelum masalah stabilitas selesai.
- Perubahan arsitektur navigasi atau penambahan React Router.
- Optimasi performa tanpa hasil profiling.

## Fase 1: Stabilitas Data dan Recovery

**Prioritas:** P0  
**Dependensi:** Tidak ada  
**Area utama:** `apps/web/src/hooks`, `apps/web/src/components`, `apps/worker/app/main.py`

### 1.1 Perbaiki autosave project

Masalah saat ini:

- `flushSave()` dapat selesai ketika save lain masih berjalan.
- Respons snapshot lama dapat digabungkan ke project aktif yang berbeda.
- Draft dapat dihapus meskipun terdapat edit yang lebih baru.
- Perpindahan project tidak menunggu atau membatalkan save sebelumnya.
- Render berpotensi dimulai sebelum revisi terbaru tersimpan.

Implementasi:

- Jadikan proses save serial dan terikat pada ID project.
- Simpan versi draft yang ikut dalam setiap request.
- Abaikan respons jika ID project respons berbeda dari project aktif.
- Hapus draft hanya jika versinya sama dengan versi yang berhasil disimpan.
- Jika edit baru muncul saat save berjalan, jalankan save berikutnya setelah request aktif selesai.
- Pastikan `flushSave()` menunggu seluruh antrean save project aktif.
- Flush perubahan sebelum membuka project lain, membuat project baru, atau memulai render.
- Pertahankan penanganan revision conflict yang sudah ada.

Kriteria selesai:

- Edit kedua yang dibuat saat PATCH pertama berjalan tetap tersimpan.
- Respons project A tidak pernah mengubah state project B.
- Render selalu menggunakan revisi terbaru.
- Status `saved` hanya muncul ketika tidak ada draft atau request tertunda.
- Revision conflict tetap ditampilkan dan tidak menimpa perubahan secara diam-diam.

Pengujian:

- Delayed PATCH diikuti edit kedua.
- Pindah project ketika save sedang berjalan.
- Memulai render ketika save sedang berjalan.
- Respons save datang setelah project aktif berubah.
- Revision conflict dengan draft yang belum tersimpan.

### 1.2 Perbaiki recovery project

Implementasi:

- Ubah CTA `Start analysis` agar benar-benar menjalankan prepare atau analyze sesuai state project.
- Pulihkan job aktif jika project dibuka kembali.
- Tampilkan aksi retry yang tepat untuk prepare/analyze yang gagal atau dibatalkan.
- Pastikan tab tujuan mengikuti hasil aksi: AI Moments setelah analysis dan Hasil Render setelah render.

Kriteria selesai:

- Project yang baru dibuat lalu dibatalkan dapat dilanjutkan dari library.
- CTA pada Ringkasan selalu menjalankan aksi yang ditampilkan pada labelnya.
- Project dengan job aktif kembali menampilkan progress setelah dibuka.

### 1.3 Samakan kontrak URL YouTube

Implementasi:

- Gunakan daftar hostname yang sama di frontend dan worker.
- Dukung atau tolak `m.youtube.com` dan `music.youtube.com` secara konsisten.
- Pertahankan validasi server sebagai sumber kebenaran.

Kriteria selesai:

- Setiap URL yang dianggap valid oleh frontend diterima worker.
- URL non-YouTube dan hostname spoofing tetap ditolak.

### 1.4 Tangani kegagalan cancellation

Implementasi:

- Tangkap kegagalan request cancel.
- Jangan keluar dari state processing bila worker belum mengonfirmasi cancellation.
- Tampilkan error yang dapat dicoba ulang dan pertahankan polling job.

Kriteria selesai:

- UI tidak menampilkan job selesai atau batal sebelum status backend berubah.
- Pengguna dapat mencoba cancellation kembali setelah kegagalan jaringan.

## Fase 2: Desktop Vertical Slice

**Prioritas:** P0  
**Dependensi:** Fase 1 selesai  
**Area utama:** `apps/desktop`, `apps/worker`, `scripts/package-worker.ps1`

### 2.1 Buat worker executable yang valid

Implementasi:

- Tambahkan entry point executable yang menjalankan Uvicorn pada loopback.
- Pastikan shutdown signal menghentikan server dengan bersih.
- Sertakan font dan runtime asset yang dibutuhkan caption/render.
- Pisahkan dependency packaging dari runtime jika diperlukan.

Kriteria selesai:

- Binary worker tetap hidup setelah dijalankan.
- `/api/health` dapat diakses dari desktop app.
- Caption render menemukan font dari paket instalasi.
- Worker berhenti ketika aplikasi desktop ditutup.

### 2.2 Aktifkan dan kelola sidecar Tauri

Implementasi:

- Daftarkan worker melalui `externalBin`.
- Aktifkan permission spawn yang minimum dan deterministik.
- Panggil `start_worker` saat startup aplikasi.
- Hindari worker ganda jika instance sudah berjalan.
- Tunggu health check sebelum frontend dianggap siap.
- Tangani startup timeout, crash, restart, dan shutdown.
- Jadikan script packaging idempotent.

Kriteria selesai:

- Pengguna tidak perlu menjalankan worker secara manual.
- Startup failure menghasilkan pesan yang dapat ditindaklanjuti.
- Menjalankan packaging berulang tidak menggandakan konfigurasi.
- Tidak ada proses worker yatim setelah desktop app ditutup.

### 2.3 Amankan credential desktop

Implementasi:

- Simpan installation credential melalui Stronghold.
- Migrasikan alur onboarding agar tidak bergantung pada JSON plaintext.
- Jangan log token, invite, atau credential provider.
- Tentukan alur reset credential yang eksplisit.

Kriteria selesai:

- Credential tidak tersimpan sebagai plaintext di app data.
- Desktop dapat memulihkan credential setelah restart.
- Reset credential tidak menghapus project atau output pengguna.

### 2.4 Verifikasi integrasi WebView2

Implementasi:

- Verifikasi origin aktual Tauri pada Windows.
- Izinkan hanya origin desktop dan development yang benar pada CORS.
- Tambahkan CSP minimum untuk bundled frontend dan worker loopback.
- Uji upload, stream video, dan download output dari desktop build.

Kriteria selesai:

- Production desktop tidak bergantung pada origin development.
- Request dari origin yang tidak diizinkan ditolak.
- Semua workflow utama berjalan dari build Tauri.

### 2.5 Smoke test installer Windows

Alur wajib:

```text
install aplikasi
-> buka desktop
-> sidecar hidup
-> health dan capability probe berhasil
-> upload fixture pendek
-> prepare/analyze melalui mock gateway
-> edit satu moment
-> render MP4
-> restart desktop
-> project dan output tetap tersedia
-> uninstall tidak menghapus output pengguna tanpa konfirmasi
```

## Fase 3: Hardening Worker Lokal

**Prioritas:** P0  
**Dependensi:** Desain token harus selaras dengan lifecycle desktop pada Fase 2  
**Area utama:** `apps/worker/app/main.py`, API client web, konfigurasi desktop

### 3.1 Tambahkan autentikasi API lokal

Implementasi:

- Buat bearer token acak per instalasi atau sesi desktop.
- Wajibkan token pada semua endpoint project, job, media, output, patch, delete, dan cancel.
- Sisakan health check minimum tanpa data sensitif bila dibutuhkan untuk readiness.
- Kirim token dari API client tanpa memasukkannya ke log atau URL.
- Gunakan perbandingan token yang aman.

Kriteria selesai:

- Request tanpa token tidak dapat membaca project, transcript, source, atau output.
- Token tidak muncul pada log worker, frontend, dan crash report.
- Demo mode dan development workflow tetap dapat dijalankan secara eksplisit.

### 3.2 Batasi upload dan penggunaan disk

Implementasi:

- Tetapkan batas byte upload yang terdokumentasi.
- Tolak `Content-Length` yang melebihi batas sebelum menulis body.
- Tetap hitung byte saat streaming untuk request tanpa `Content-Length`.
- Hapus file parsial setelah request gagal atau dibatalkan.
- Periksa ruang disk minimum sebelum upload dan render.
- Batasi jumlah upload bersamaan.

Kriteria selesai:

- Upload yang melewati batas dihentikan sebelum memenuhi disk.
- File parsial tidak tertinggal setelah failure.
- Error disk penuh memiliki pesan dan status API yang jelas.

### 3.3 Amankan error release

Implementasi:

- Jangan mengirim raw exception atau path internal pada response release.
- Simpan detail diagnostik pada log lokal yang sesuai.
- Gunakan error code stabil agar frontend dapat menentukan pesan dan retry.

Kriteria selesai:

- Response release tidak membocorkan stack trace, secret, atau path sensitif.
- Error tetap dapat didiagnosis dari log lokal.

## Fase 4: Reliability dan Release Engineering

**Prioritas:** P1  
**Dependensi:** Fase P0 selesai

### 4.1 Perbaiki progress render

- Teruskan context `job` dan jumlah clip ke `render_one_clip`.
- Laporkan tahap vision analysis dan encoding secara nyata.
- Pastikan progress monoton dan tidak mencapai 100% sebelum output atomik selesai.

### 4.2 Lindungi project selama job aktif

- Tolak atau revision-lock PATCH yang berkonflik dengan job aktif.
- Tentukan field yang tetap boleh diedit jika memang aman.
- Tambahkan test untuk dua window/client yang mengubah project bersamaan.

### 4.3 Verifikasi runtime yang diunduh

- Pin versi Deno.
- Verifikasi SHA-256 atau signature sebelum ekstraksi.
- Hapus download gagal dan jangan menjalankan binary yang tidak terverifikasi.

### 4.4 Pin dependency

- Ganti versi `latest` pada manifest npm dengan versi exact dari lockfile yang telah diverifikasi.
- Buat constraints atau lock Python yang reproducible.
- Pisahkan dependency development/packaging dari runtime worker.
- Review upgrade dependency sebagai perubahan terpisah.

### 4.5 Tambahkan CI Windows

Pipeline minimum:

1. Install npm dependency dari lockfile.
2. Siapkan Python 3.11 dan dependency terkunci.
3. Jalankan typecheck dan unit test web.
4. Build dan test gateway.
5. Jalankan worker tests.
6. Jalankan production build web.
7. Jalankan Playwright.
8. Jalankan Cargo check dengan lockfile.
9. Package worker dan lakukan sidecar smoke test.

### 4.6 Tambahkan quality checks minimum

- Tambahkan ESLint untuk TypeScript/React.
- Tambahkan Ruff untuk Python.
- Jalankan formatter sebagai check terpisah.
- Hindari aturan kosmetik yang menghasilkan churn tanpa manfaat.

### 4.7 Perbaiki aksesibilitas Manual Cut

- Tambahkan semantics dialog, `aria-modal`, label, focus trap, Escape, dan focus restore.
- Uji dengan keyboard dan Axe.

## Fase 5: Maintainability

**Prioritas:** P2  
**Dependensi:** Pipeline stabil dan test integrasi tersedia

### 5.1 Refactor berdasarkan boundary yang sudah terbukti

- Pisahkan `apps/worker/app/main.py` menjadi API, jobs, provider, render, dan streaming hanya jika perubahan P0/P1 menunjukkan boundary tersebut stabil.
- Pisahkan dialog/editor/timeline/manual cut dari `review.tsx` tanpa mengubah perilaku.
- Hindari interface, factory, atau abstraction layer yang hanya memiliki satu implementasi.

### 5.2 Sinkronkan dokumentasi dan versi

- Samakan versi package, FastAPI app, dan health response.
- Tandai item yang sudah selesai pada `plan-sidebar.md`.
- Ubah `docs/UI_UX_AUDIT.md` menjadi status audit yang membedakan resolved dan remaining.
- Dokumentasikan setup development, packaging, release, backup, dan recovery.

### 5.3 Evaluasi hardware encoder

- Ukur waktu render dan kualitas pada perangkat target.
- Tambahkan encoder hardware hanya bila peningkatannya nyata.
- Pertahankan `libx264` sebagai fallback.
- Uji kompatibilitas output sebelum menjadikannya default.

## Urutan Eksekusi

| Urutan | Deliverable | Exit Gate |
| --- | --- | --- |
| 1 | Autosave dan recovery stabil | Seluruh regression test Fase 1 lulus |
| 2 | Desktop sidecar operasional | Smoke test desktop lokal lulus |
| 3 | Worker terautentikasi dan upload dibatasi | Security boundary test lulus |
| 4 | Installer closed beta | Smoke test pada Windows bersih lulus |
| 5 | CI dan dependency reproducible | Pipeline bersih dari checkout baru |
| 6 | Refactor dan optimasi terukur | Tidak ada regresi workflow utama |

## Validasi

Validasi dasar setelah setiap perubahan:

```powershell
npm.cmd run typecheck
npm.cmd run test:web
npm.cmd run build:gateway
npm.cmd run test:gateway
.\.venv\Scripts\python.exe -m pytest apps\worker\tests
npm.cmd run build
npm.cmd run test:e2e
cargo check --locked --offline --manifest-path apps\desktop\src-tauri\Cargo.toml
```

Validasi packaging setelah Fase 2:

```powershell
.\scripts\package-worker.ps1
npm.cmd run build --workspace @cuttoclip/desktop
```

Selain command di atas, release candidate wajib menjalani smoke test pipeline nyata menggunakan video fixture pendek dan mock gateway agar tidak memanggil provider eksternal.

## Definition of Done Closed Beta

Closed beta siap dibagikan jika seluruh kondisi berikut terpenuhi:

- Tidak ada test P0 yang gagal.
- Autosave aman terhadap edit paralel, perpindahan project, dan render.
- Installer menjalankan worker tanpa setup Python manual.
- Credential disimpan secara aman dan tidak muncul di log.
- API worker menolak client tanpa token.
- Upload dan render mempunyai perlindungan ruang disk.
- Project dapat dipulihkan setelah aplikasi atau worker restart.
- Pipeline upload sampai render lulus pada Windows bersih.
- CI dapat mengulang build dan test dari checkout baru.
- Batasan closed beta dan prosedur recovery terdokumentasi.

## Risiko Utama

| Risiko | Dampak | Mitigasi |
| --- | --- | --- |
| Autosave mencampur revisi project | Kehilangan atau korupsi edit | Save queue project-scoped dan regression test |
| Sidecar gagal hidup pada mesin tester | Aplikasi tidak dapat digunakan | Health-gated startup dan installer smoke test |
| Token lokal bocor | Akses ke media dan transcript | Stronghold, redaction, dan token di header |
| Upload memenuhi disk | Worker atau sistem menjadi tidak stabil | Byte limit, free-space check, dan cleanup file parsial |
| Packaging tidak menyertakan asset | Render caption gagal | Asset manifest dan packaged render test |
| Dependency berubah tanpa review | Build tidak reproducible | Exact pin, lockfile, dan CI |

## Catatan Kondisi Saat Ini

- Workspace belum terdeteksi sebagai Git repository, sehingga diff, branch, dan histori secret belum dapat diaudit.
- Build output dan cache lokal ada, tetapi tidak menjadi bukti bahwa source terkini telah lulus validasi.
- `plan-sidebar.md` dan sebagian `docs/UI_UX_AUDIT.md` sudah terimplementasi dan perlu disinkronkan pada Fase 5.
- Tauri shell saat ini compile-ready, tetapi worker sidecar dan installer belum menjadi alur operasional end-to-end.
