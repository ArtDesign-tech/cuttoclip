# Audit UI/UX CuttoClip

**Tanggal audit:** 15 Juli 2026  
**Area yang diaudit:** aplikasi web CuttoClip  
**Viewport yang ditinjau:** desktop 1440 × 1000 dan mobile 390 × 844  
**Status validasi:** Playwright E2E lulus 2/2 skenario

## Ringkasan eksekutif

CuttoClip sudah memiliki identitas visual yang kuat, alur kerja yang mudah dipahami, dan feedback proses yang cukup jelas. Masalah utamanya bukan pada estetika, tetapi pada kenyamanan menyelesaikan tugas:

1. Hero halaman **Create Clip** terlalu dominan, terutama pada mobile.
2. CTA utama **Find my clips** terpisah secara visual dari input dan Creative Brief.
3. Status **Ready to process** dapat muncul ketika sumber belum diisi.
4. Kartu AI Moments terlalu seragam karena memakai Signal Map yang sama.
5. Metadata kecil dan penggunaan uppercase/monospace yang berlebihan mengurangi keterbacaan.
6. Editor menyediakan banyak kontrol, tetapi tidak memiliki aksi **Done** atau indikator autosave yang cukup eksplisit.
7. Drawer mobile dapat menutupi konten ketika state sidebar desktop terbawa saat breakpoint berubah.

Rekomendasi implementasi utama adalah memperjelas readiness form, mendekatkan CTA ke konteks input, mengurangi tinggi hero mobile, dan memberi kepastian penyimpanan di editor.

## Cakupan dan metode audit

Audit dilakukan melalui:

- Penelusuran struktur `App.tsx`, komponen, hooks, styling, i18n, dan pengujian E2E.
- Pemeriksaan alur source → processing → AI Moments → editor.
- Rendering aplikasi dalam demo mode pada desktop dan mobile.
- Pemeriksaan hierarki visual, keterbacaan, kepadatan informasi, feedback sistem, responsivitas, dan aksesibilitas dasar.
- Peninjauan screenshot halaman Create Clip desktop/mobile, Processing, AI Moments, editor, serta workspace mobile.

## Hal yang sudah baik

### 1. Identitas produk kuat

Warna gelap, aksen lime/coral, tipografi besar, serta elemen Signal Map membuat produk mudah dikenali dan terasa konsisten sebagai aplikasi kreatif.

### 2. Alur proses mudah dipahami

Urutan **Source → Transcript → Moments → Clips** muncul secara konsisten. Hal ini membantu pengguna memahami posisi mereka di dalam workflow.

### 3. Feedback processing jelas

Layar processing sudah menyediakan:

- judul status yang menonjol;
- persentase progress;
- tahapan proses;
- penjelasan bahwa aplikasi dapat dimuat ulang;
- tombol pembatalan job.

### 4. Pemilihan dan rendering clip mudah ditemukan

Status clip terpilih terlihat pada kartu. Action bar render di desktop dan mobile juga memiliki visibilitas yang baik.

### 5. Fondasi aksesibilitas sudah tersedia

Implementasi sudah mencakup `focus-visible`, reduced motion, toast dengan status semantik, drawer mobile, pengembalian fokus, dan audit Axe pada sebagian layar.

## Temuan dan rekomendasi

## P0 — Perubahan paling berdampak

### P0.1 — Kurangi dominasi hero Create Clip

**Temuan**

Headline memakan area sangat besar. Pada mobile 390 × 844, headline menggunakan beberapa baris dan mendorong Creative Brief serta CTA jauh ke bawah.

**Dampak**

- Pengguna baru membutuhkan lebih banyak scroll sebelum dapat menyelesaikan tugas utama.
- Input dan CTA kalah dominan dari pesan pemasaran.
- Halaman terasa seperti landing page, bukan workspace produktivitas.

**Rekomendasi**

- Kurangi ukuran headline desktop sekitar 15–20%.
- Gunakan ukuran sekitar `40–44px` pada mobile.
- Batasi deskripsi menjadi maksimal dua atau tiga baris pada desktop.
- Sembunyikan atau ringkas Signal Map dekoratif pada viewport pendek.
- Setelah pengguna memiliki project, gunakan varian hero yang lebih ringkas.

### P0.2 — Satukan CTA dengan input dan Creative Brief

**Temuan**

Pada desktop, tombol **Find my clips** berada di kanan bawah dan terasa terpisah dari kartu URL maupun Creative Brief. Pada mobile, tombol tidak terlihat pada viewport pertama.

**Dampak**

Pengguna harus memindai area yang luas untuk menemukan langkah berikutnya dan hubungan antara input dengan aksi utama menjadi kurang jelas.

**Rekomendasi**

- Tempatkan CTA di dalam Creative Brief atau action bar tepat di bawah kedua kartu.
- Gunakan lebar penuh pada mobile.
- Setelah sumber valid, CTA mobile dapat dibuat sticky di bagian bawah.
- Letakkan helper text atau alasan disabled tepat di dekat CTA.

### P0.3 — Pisahkan status sistem dan validasi form

**Temuan**

Pesan **Ready to process** dapat tampil ketika URL atau file belum dipilih, sedangkan CTA belum siap digunakan.

**Dampak**

Pesan status bertentangan dengan kondisi form dan dapat membuat pengguna mengira terjadi error pada tombol.

**Rekomendasi state**

| Kondisi | Pesan yang disarankan |
|---|---|
| Worker siap, sumber kosong | `System ready · Add a video source to continue` |
| URL tidak valid | `Enter a valid public YouTube URL` |
| File tidak didukung | `Choose a supported video file` |
| Form valid | `Ready to find clips` |
| Worker tidak tersedia | `Local processing system unavailable` |

Status worker sebaiknya tetap berada di shell, sedangkan readiness form berada di dekat CTA.

### P0.4 — Tambahkan kepastian penyimpanan di editor

**Temuan**

Editor hanya menyediakan tombol tutup `X`. Tidak ada tombol **Done** atau indikator autosave yang terlihat jelas di dalam dialog.

**Dampak**

Pengguna dapat ragu apakah perubahan title, hook, trim, frame, atau caption sudah tersimpan.

**Rekomendasi**

- Tambahkan indikator `Saving…`, `Saved`, atau `Save failed` pada header editor.
- Tambahkan tombol utama **Done**.
- Pertahankan autosave, tetapi tampilkan statusnya secara eksplisit.
- Jika save gagal, konfirmasi sebelum editor ditutup.
- Kembalikan fokus ke tombol **Edit clip** setelah editor ditutup.

## P1 — Peningkatan kenyamanan dan keterbacaan

### P1.1 — Gunakan visual unik pada setiap AI Moment

**Temuan**

Semua kartu menggunakan Signal Map generik yang hampir sama. Perbedaan utama baru terlihat setelah membaca judul, skor, dan timestamp.

**Dampak**

Pengguna sulit membandingkan moment secara visual dan harus membaca setiap kartu satu per satu.

**Rekomendasi**

- Gunakan thumbnail atau keyframe unik dari timestamp clip.
- Pertahankan waveform sebagai overlay kecil, bukan visual utama.
- Tampilkan crop guide hanya ketika kartu di-hover atau dibuka.
- Jika thumbnail belum tersedia, gunakan frame placeholder yang berbeda berdasarkan timestamp.

### P1.2 — Tingkatkan ukuran dan kontras metadata

**Temuan**

Sebagian metadata menggunakan uppercase/monospace sekitar 8–10px dengan warna sekunder yang redup.

**Dampak**

Informasi seperti timestamp, preset, dan status menjadi melelahkan untuk dibaca, terutama di layar kecil atau laptop dengan scaling tinggi.

**Rekomendasi**

- Gunakan minimal `11–12px` untuk metadata penting.
- Gunakan line-height sekitar `1.4`.
- Batasi monospace untuk timestamp, durasi, skor, dan informasi teknis.
- Gunakan sentence case untuk label biasa.
- Naikkan kontras teks sekunder tanpa menghilangkan hierarki visual.

> Lulus Axe tidak selalu berarti nyaman dibaca. Ukuran teks dan kepadatan tetap perlu dievaluasi secara visual.

### P1.3 — Sederhanakan kartu AI Moments

**Temuan**

Kartu menampilkan banyak lapisan informasi: Signal Map, waveform, nomor, durasi, label AI Pick, skor, judul, alasan, timestamp, preset, dan tombol Edit.

**Rekomendasi hierarki kartu**

1. Thumbnail/keyframe.
2. Durasi dan selected state.
3. Judul moment.
4. Satu baris alasan pemilihan.
5. Timestamp dan skor AI.
6. Tombol **Edit clip**.

Preset frame/caption dapat dipindahkan ke chip ringkas atau hanya ditampilkan di editor.

### P1.4 — Optimalkan editor untuk viewport pendek

**Temuan**

Preview memperoleh area yang besar, sedangkan timeline berada dekat batas bawah modal. Pada laptop yang lebih pendek, kontrol berpotensi terasa sempit.

**Rekomendasi**

- Berikan scroll independen pada inspector kanan.
- Pertahankan timeline tetap terlihat atau buat ukuran area preview dapat diubah.
- Kurangi tinggi preview pada viewport pendek.
- Jadikan header editor sticky.
- Pastikan action penting tidak tertutup pada tinggi 768px.

### P1.5 — Normalisasi state drawer saat breakpoint berubah

**Temuan**

Fresh mobile membuka workspace dengan drawer tertutup. Namun, ketika state sidebar desktop terbawa ke viewport mobile, drawer dapat tetap terbuka dan menutupi konten.

**Rekomendasi**

- Tutup drawer ketika masuk breakpoint mobile, kecuali pengguna membukanya secara eksplisit.
- Kunci scroll konten di belakang drawer.
- Tambahkan focus trap dan tutup drawer melalui `Escape`.
- Kembalikan fokus ke hamburger button setelah drawer ditutup.

## P2 — Konsistensi sistem desain

### P2.1 — Tegaskan semantik warna

Gunakan aturan konsisten:

- **Coral:** primary action.
- **Lime:** selected, success, atau ready.
- **Merah:** destructive dan error.
- **Netral:** secondary action.

Hindari menggunakan lime untuk navigasi utama jika warna tersebut juga berarti success atau selected.

### P2.2 — Kurangi dekorasi berulang

Label dan visual **Signal Map** tidak perlu tampil pada setiap konteks. Gunakan terutama pada:

- Create Clip;
- Processing;
- empty state atau placeholder.

Pada AI Moments, prioritaskan konten video yang sebenarnya.

### P2.3 — Rapikan cascade CSS lama dan baru

Styling shell/review lama dan blok modern saling mengoverride. Konsolidasikan selector agar:

- perubahan layout lebih mudah diprediksi;
- breakpoint tidak menghasilkan state yang bertentangan;
- token typography dan spacing digunakan secara konsisten;
- maintenance komponen menjadi lebih aman.

### P2.4 — Perluas audit aksesibilitas

Tambahkan validasi untuk:

- focus trap pada editor dan modal manual cut;
- navigasi keyboard pada timeline;
- Axe pada AI Moments, Results, Settings, dan Library;
- alur mobile penuh dari source sampai results;
- viewport laptop 1366 × 768;
- pembesaran teks 200%;
- touch target minimal sekitar 44 × 44px;
- status autosave dan processing agar diumumkan secara semantik.

## Struktur Create Clip yang disarankan

```text
Create clip
Import a video and find its best moments.

[ YouTube link | Local video ]
[ URL input                              ] [Paste]

Creative brief
[ Moments: 3 ] [Duration: 15–90s] [Language: Auto]

System ready
Add a video source to continue

[ Find my clips ]
```

### Desktop

Form dapat menggunakan dua kolom, tetapi CTA harus berada tepat di bawah kedua kolom agar hubungan antarbagian tetap jelas.

### Mobile

Gunakan satu kolom. Setelah sumber valid, CTA dapat menjadi sticky bottom action dengan safe-area padding.

## Urutan implementasi

### Fase 1 — Kejelasan task utama

1. Perbaiki readiness dan validasi source.
2. Pindahkan CTA lebih dekat ke form.
3. Kurangi tinggi hero pada desktop, mobile, dan viewport pendek.
4. Tambahkan **Done** serta status autosave pada editor.

### Fase 2 — Efisiensi pemindaian

1. Ganti Signal Map kartu dengan thumbnail/keyframe.
2. Sederhanakan informasi kartu.
3. Tingkatkan ukuran dan kontras metadata.
4. Optimalkan editor untuk tinggi layar yang terbatas.

### Fase 3 — Konsistensi dan hardening

1. Normalisasi state drawer responsive.
2. Satukan semantik warna.
3. Konsolidasikan cascade CSS.
4. Perluas E2E dan audit aksesibilitas.

## Kriteria penerimaan

### Create Clip

- Pesan readiness tidak menyatakan siap jika sumber belum valid.
- Alasan CTA disabled terlihat dan mudah dipahami.
- CTA berada dalam konteks form dan mudah ditemukan pada desktop/mobile.
- Pada 390 × 844, pengguna dapat mengakses sumber dan aksi utama tanpa kehilangan konteks akibat hero yang terlalu tinggi.

### AI Moments

- Setiap moment dapat dibedakan secara visual tanpa membaca seluruh isi kartu.
- Timestamp, skor, dan preset tetap terbaca pada mobile.
- Selected state dan jumlah clip yang akan dirender konsisten.

### Editor

- Pengguna melihat status penyimpanan.
- Tersedia aksi **Done** yang eksplisit.
- Focus trap bekerja dan fokus dikembalikan setelah dialog ditutup.
- Timeline dan kontrol utama tetap dapat diakses pada tinggi 768px.

### Responsive

- Drawer tertutup saat pertama masuk mobile.
- Drawer tidak meninggalkan konten dalam keadaan bergeser atau tertutup setelah breakpoint berubah.
- CTA render tidak menutupi kartu terakhir.
- Tidak ada horizontal overflow pada 390px.

## Lokasi implementasi

| File | Area perubahan |
|---|---|
| `apps/web/src/components/source.tsx` | Hero, input source, readiness, validasi, dan CTA |
| `apps/web/src/components/review.tsx` | Kartu moment, editor, timeline, Done, dan autosave state |
| `apps/web/src/components/shell.tsx` | Sidebar/drawer dan perpindahan breakpoint |
| `apps/web/src/components/processing.tsx` | Feedback proses dan semantic status |
| `apps/web/src/styles.css` | Typography, spacing, warna, breakpoint, dan cascade |
| `apps/web/src/App.tsx` | Integrasi save state, focus management, dan view state |
| `apps/web/e2e/redesign.spec.ts` | Pengujian responsive, focus trap, dan Axe tambahan |

## Kesimpulan

Desain CuttoClip tidak memerlukan redesign total. Fondasi visual dan workflow sudah baik. Perubahan terbesar sebaiknya difokuskan pada:

1. mempercepat akses ke tugas utama;
2. menghilangkan status yang ambigu;
3. meningkatkan keterbacaan;
4. membedakan AI Moments secara visual;
5. memberi kepastian penyimpanan di editor;
6. memperkuat perilaku responsive dan aksesibilitas.

Dengan urutan tersebut, UI tetap mempertahankan karakter visual CuttoClip sambil menjadi lebih nyaman, jelas, dan efisien digunakan.
