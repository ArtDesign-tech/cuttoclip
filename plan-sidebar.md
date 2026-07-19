# Rencana Implementasi Sidebar dan Project Saya

## Ringkasan

- Tambahkan sidebar utama berisi **Buat Clip**, **Project Saya**, dan **Pengaturan**.
- Sidebar desktop berukuran sekitar 248 px dan dapat diciutkan menjadi rail ikon 72 px; pada layar di bawah 900 px berubah menjadi drawer.
- **Project Saya** menjadi pusat AI Moments, editor caption, dan hasil render melalui tab **Ringkasan**, **AI Moments**, dan **Hasil Render**.
- Pertahankan identitas visual carbon, coral, dan signal green saat ini. Penanda menu aktif menggunakan bentuk trim/signal mark, bukan pill navigasi generik.

## Perubahan Implementasi

### Navigasi dan app shell

- Pisahkan state navigasi menjadi `create`, `projects`, `project`, dan `settings`, sementara state proses `prepare/analyze/render` tetap digunakan untuk pekerjaan media.
- Simpan status sidebar dan tab terakhir setiap project di `localStorage`.
- Sidebar memuat logo, tiga menu utama, tombol ciutkan, serta status worker di footer.
- Sederhanakan header menjadi tombol drawer untuk mobile, judul project aktif, dan status autosave.
- Sediakan tooltip, `aria-current`, navigasi keyboard, penutupan drawer dengan Escape, dan pengembalian fokus ke tombol pembuka.

### Halaman Project Saya

- Ambil daftar ringkas project, urutkan berdasarkan waktu pembaruan terbaru, dan sediakan pencarian berdasarkan nama sumber.
- Setiap kartu menampilkan thumbnail frame atau fallback signal, sumber, durasi, jumlah AI Moments, jumlah hasil render, waktu pembaruan, dan status proses.
- Tangani state loading, kosong, worker offline, gagal memuat, serta project yang sedang diproses.
- Tombol **Buka project** memuat data lengkap dan memulihkan job aktif jika ada.
- Tombol **Hapus** menampilkan konfirmasi dengan nama project dan keterangan bahwa hasil render tetap disimpan.
- Jika project sedang memiliki job aktif, penghapusan ditolak sampai proses selesai atau dibatalkan.

### Workspace project

- **Ringkasan:** metadata video, status transkrip, jumlah moments, clip terpilih, hasil render, preset default, serta CTA yang ditentukan dari kondisi project.
- **AI Moments:** gunakan kembali galeri moment yang ada, termasuk pilih semua, analisis ulang, tambah manual cut, dan editor clip dalam modal.
- **Editor modal:** tetap menjadi lokasi trim, layout, judul, hook, dan template caption per clip; template dapat diterapkan ke semua clip.
- **Hasil Render:** gunakan kembali galeri hasil, download, buka folder, render ulang, retry hasil gagal, serta penanda hasil usang.
- Setelah analisis selesai, arahkan ke **AI Moments**; setelah render selesai, arahkan ke **Hasil Render**.

### Halaman Pengaturan

- Pindahkan kontrol bahasa EN/ID ke halaman ini dan pertahankan penyimpanan lokal yang sudah ada.
- Tampilkan status worker, FFmpeg, FFprobe, yt-dlp, gateway, vision mode, encoder, format yang didukung, data root, dan output root.
- Sediakan tombol **Periksa ulang sistem**.
- Folder output dan kualitas render hanya ditampilkan sebagai informasi pada v1, tanpa kontrol perubahan.

## API dan Tipe

- Tambahkan `ProjectSummary` dengan field `id`, `sourceLabel`, `sourceKind`, `durationSeconds`, `resolution`, `transcriptReady`, `status`, `createdAt`, `updatedAt`, `candidateCount`, `outputCount`, dan `failedOutputCount`.
- Tambahkan `GET /api/projects/summaries`, terurut berdasarkan `updatedAt` terbaru, agar daftar project tidak mengirim transkrip lengkap.
- Tambahkan `DELETE /api/projects/{project_id}` dengan respons `204`.
- Penghapusan membersihkan data project internal, cache, dan riwayat job, tetapi mempertahankan folder hasil render dan tidak pernah menghapus file sumber eksternal milik pengguna.
- Tambahkan capability `project-library` dan `project-delete`, beserta implementasi ekuivalen untuk Demo Mode.
- API daftar project penuh yang sudah ada tetap dipertahankan untuk kompatibilitas.

## Pengujian dan Kriteria Selesai

- Uji worker untuk ringkasan project, urutan project, jumlah moments/output, penghapusan data internal, penjagaan file output, dan penolakan saat job aktif.
- Uji API web untuk daftar, normalisasi summary, delete, error offline, dan Demo Mode.
- Uji UI untuk perpindahan menu, status aktif, collapse persistence, pencarian, empty state, membuka project, tab project, dan dialog hapus.
- Uji pemulihan job aktif dan tab terakhir setelah aplikasi dimuat ulang.
- Uji Playwright pada desktop, tablet, dan mobile untuk drawer, overflow, fokus keyboard, dan aksesibilitas.
- Validasi akhir dengan `npm.cmd run typecheck`, test web, test worker, test E2E, dan build produksi.
- Fitur dinyatakan selesai ketika seluruh workflow lama tetap bekerja dan pengguna dapat membuat, menemukan, membuka, melanjutkan, mengedit, merender, serta menghapus project melalui sidebar baru.

## Asumsi yang Dikunci

- Tidak ada rename, duplikasi, arsip, pengubahan folder output, atau preset kualitas render pada v1.
- Tidak menambahkan React Router; navigasi menggunakan state aplikasi dan `localStorage`, sesuai arsitektur desktop lokal saat ini.
- AI Moments, template caption, dan hasil render tidak menjadi menu sidebar terpisah.
- Semua teks baru tersedia dalam bahasa Indonesia dan Inggris.
