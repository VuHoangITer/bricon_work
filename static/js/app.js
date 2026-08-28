/* ==========================================================================
   BRICON — giao việc & chấm công
   ========================================================================== */

/* ---------------------------------------------------------------- định vị */
function layViTri(form, nutBam) {
  const oTrangThai = form.querySelector('[data-vi-tri]');
  const dat = (chu, mau) => {
    if (!oTrangThai) return;
    oTrangThai.textContent = chu;
    oTrangThai.style.color = mau || 'var(--muc-mo)';
  };

  if (!navigator.geolocation) {
    dat('Trình duyệt này không hỗ trợ định vị. Dùng Chrome hoặc Safari.', 'var(--lam-lai)');
    return;
  }
  if (!window.isSecureContext) {
    dat('Cần truy cập bằng https thì trình duyệt mới cho lấy vị trí.', 'var(--lam-lai)');
    return;
  }

  nutBam.disabled = true;
  dat('Đang lấy vị trí…');

  navigator.geolocation.getCurrentPosition(
    (vt) => {
      form.querySelector('[name=lat]').value = vt.coords.latitude;
      form.querySelector('[name=lng]').value = vt.coords.longitude;
      form.querySelector('[name=do_chinh_xac]').value = vt.coords.accuracy;
      const saiSo = Math.round(vt.coords.accuracy);
      if (saiSo > 100) {
        dat('Sai số ' + saiSo + 'm — quá lớn. Ra chỗ thoáng rồi bấm lại.', 'var(--lam-lai)');
        nutBam.disabled = false;
        return;
      }
      dat('Đã có vị trí, sai số ' + saiSo + 'm. Đang gửi…', 'var(--hoan-thanh)');
      form.submit();
    },
    (loi) => {
      nutBam.disabled = false;
      const chu = {
        1: 'Bạn đã chặn quyền vị trí. Mở cài đặt trình duyệt và cho phép lại cho trang này.',
        2: 'Không xác định được vị trí. Bật GPS rồi thử lại.',
        3: 'Lấy vị trí quá lâu. Ra ngoài trời rồi thử lại.'
      }[loi.code] || 'Lỗi định vị: ' + loi.message;
      dat(chu, 'var(--lam-lai)');
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
  );
}

function khoiTaoChamCong() {
  document.querySelectorAll('form[data-can-vi-tri]').forEach((form) => {
    const nut = form.querySelector('[data-nut-gui]');
    if (!nut) return;
    nut.addEventListener('click', (e) => {
      e.preventDefault();
      layViTri(form, nut);
    });
  });
}

/* ------------------------------------------------- gửi đối chứng công việc */
function khoiTaoDoiChung(gioiHanMB) {
  const form = document.getElementById('form-doi-chung');
  if (!form) return;

  const oTep = document.getElementById('chon-tep');           // input ẩn, thực sự gửi lên server
  const oTepAnhVideo = document.getElementById('chon-anh-video');
  const oTepKhac = document.getElementById('chon-tep-khac');
  const xemTruoc = document.getElementById('xem-truoc');
  const nutGui = document.getElementById('nut-gui');
  const banGhi = [];               // các File ghi âm tạo trong trình duyệt
  let daChon = [];                 // TOÀN BỘ ảnh/video/tệp đã chọn, gộp qua nhiều lần bấm

  // App Zalo mở link bằng webview riêng của nó, webview này có bug đã biết
  // (report ngay trên forum dev của Zalo): <input multiple> bị vô hiệu,
  // chỉ chọn được 1 tệp/lần dù code đúng chuẩn. Không có cách nào sửa từ
  // phía web — chỉ báo cho nhân viên biết để họ mở bằng trình duyệt thật.
  const canhBaoZalo = document.getElementById('canh-bao-zalo');
  if (canhBaoZalo && /zalo/i.test(navigator.userAgent)) {
    canhBaoZalo.style.display = 'block';
  }

  function tongDungLuong() {
    let n = 0;
    for (const f of daChon) n += f.size;
    for (const f of banGhi) n += f.size;
    return n;
  }

  function kiemTraDungLuong() {
    const mb = tongDungLuong() / 1048576;
    const qua = mb > gioiHanMB;
    nutGui.disabled = qua;
    nutGui.textContent = qua
      ? `Vượt giới hạn (${mb.toFixed(1)}MB / ${gioiHanMB}MB)`
      : 'Gửi đối chứng cho sếp';
  }

  // Đồng bộ lại oTep.files từ mảng daChon — trình duyệt luôn GHI ĐÈ
  // oTep.files mỗi lần người dùng mở hộp thoại chọn tệp, nên phải tự gộp
  // trong JS rồi gán lại bằng DataTransfer, không thì mỗi lần bấm "Chọn
  // tệp" thêm sẽ mất hết những tệp đã chọn trước đó (đây là nguyên nhân
  // chính khiến không "chọn nhiều được" khi phải mở lại hộp thoại — VD
  // chọn ảnh trước, mở lại để chọn thêm video).
  function dongBoOTep() {
    const dt = new DataTransfer();
    for (const f of daChon) dt.items.add(f);
    oTep.files = dt.files;
  }

  function veLaiXemTruoc() {
    xemTruoc.innerHTML = '';
    daChon.forEach((f, idx) => {
      const o = document.createElement('div');
      o.className = 'tep';
      if (f.type.startsWith('image/')) {
        o.innerHTML = `<img src="${URL.createObjectURL(f)}" alt="">`;
      } else if (f.type.startsWith('video/')) {
        o.innerHTML = `<video src="${URL.createObjectURL(f)}" preload="metadata" muted playsinline></video>`;
      } else {
        o.innerHTML = `<span class="tep-khac">📄<span>${f.name.slice(0, 22)}</span></span>`;
      }
      const xoa = document.createElement('button');
      xoa.type = 'button';
      xoa.className = 'tep-xoa';
      xoa.textContent = '✕';
      xoa.title = 'Bỏ tệp này';
      xoa.addEventListener('click', () => {
        daChon.splice(idx, 1);
        dongBoOTep();
        veLaiXemTruoc();
        kiemTraDungLuong();
      });
      o.appendChild(xoa);
      xemTruoc.appendChild(o);
    });
  }

  function gomTuInput(input) {
    if (!input) return;
    input.addEventListener('change', () => {
      for (const f of input.files) {
        const daCo = daChon.some(
          (x) => x.name === f.name && x.size === f.size && x.lastModified === f.lastModified
        );
        if (!daCo) daChon.push(f);
      }
      input.value = '';   // reset để lần sau lỡ chọn lại đúng tệp cũ vẫn bắn 'change'
      dongBoOTep();
      veLaiXemTruoc();
      kiemTraDungLuong();
    });
  }
  gomTuInput(oTepAnhVideo);
  gomTuInput(oTepKhac);

  /* ---- ghi âm ---- */
  const nutGhi = document.getElementById('nut-ghi-am');
  const dsGhi = document.getElementById('ds-ghi-am');
  const dongHo = document.getElementById('dong-ho-ghi');
  let mr = null, cacDoan = [], demGio = null;

  function dinhDangHoTro() {
    // Safari iOS trả mp4, Chrome/Firefox trả webm — hỏi trình duyệt thay vì đoán
    for (const t of ['audio/webm', 'audio/mp4', 'audio/ogg']) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
  }

  if (nutGhi) {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      nutGhi.disabled = true;
      nutGhi.textContent = 'Trình duyệt không hỗ trợ ghi âm';
    }
    nutGhi.addEventListener('click', async () => {
      if (mr && mr.state === 'recording') { mr.stop(); return; }
      try {
        const luong = await navigator.mediaDevices.getUserMedia({ audio: true });
        const kieu = dinhDangHoTro();
        mr = new MediaRecorder(luong, kieu ? { mimeType: kieu } : undefined);
        cacDoan = [];
        mr.ondataavailable = (e) => e.data.size && cacDoan.push(e.data);
        mr.onstop = () => {
          luong.getTracks().forEach((t) => t.stop());
          clearInterval(demGio);
          dongHo.textContent = '';
          nutGhi.textContent = '● Bắt đầu ghi âm';
          nutGhi.classList.remove('nut--do');

          const kieuThat = mr.mimeType || kieu || 'audio/webm';
          const duoi = kieuThat.includes('mp4') ? 'm4a' : kieuThat.includes('ogg') ? 'ogg' : 'webm';
          const blob = new Blob(cacDoan, { type: kieuThat });
          const file = new File([blob], `ghi-am-${Date.now()}.${duoi}`, { type: kieuThat });
          banGhi.push(file);

          const hang = document.createElement('div');
          hang.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px';
          hang.innerHTML = `<audio controls src="${URL.createObjectURL(blob)}" style="flex:1"></audio>`;
          const xoa = document.createElement('button');
          xoa.type = 'button';
          xoa.className = 'nut nut--phu nut--nho';
          xoa.textContent = 'Xoá';
          xoa.onclick = () => {
            banGhi.splice(banGhi.indexOf(file), 1);
            hang.remove();
            kiemTraDungLuong();
          };
          hang.appendChild(xoa);
          dsGhi.appendChild(hang);
          kiemTraDungLuong();
        };
        mr.start();
        nutGhi.textContent = '■ Dừng ghi';
        nutGhi.classList.add('nut--do');
        let giay = 0;
        demGio = setInterval(() => {
          giay += 1;
          dongHo.textContent = `${String((giay / 60) | 0).padStart(2, '0')}:${String(giay % 60).padStart(2, '0')}`;
          if (giay >= 600) mr.stop();     // chặn ở 10 phút
        }, 1000);
      } catch (e) {
        alert('Không truy cập được micro. Cho phép quyền micro cho trang này rồi thử lại.');
      }
    });
  }

  form.addEventListener('submit', (e) => {
    // Gắn các bản ghi âm vào input file trước khi gửi
    if (banGhi.length && oTep) {
      const dt = new DataTransfer();
      for (const f of oTep.files) dt.items.add(f);
      for (const f of banGhi) dt.items.add(f);
      oTep.files = dt.files;
      banGhi.length = 0;
    }
    if (!oTep.files.length && !form.querySelector('#ghi_chu').value.trim()) {
      e.preventDefault();
      alert('Gửi ít nhất 1 tệp hoặc viết mô tả kết quả.');
      return;
    }
    nutGui.disabled = true;
    nutGui.textContent = 'Đang tải lên…';
  });
}

document.addEventListener('DOMContentLoaded', khoiTaoChamCong);