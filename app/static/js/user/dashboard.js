// Global variables
let map;
let currentCameraId = window.FEATURED_CCTV_IDX || null;
let cctvData = window.CCTV_DATA || [];
let markers = [];
let isDetectionActive = false;
let detectionStartTime = null;
let detectionPaused = false;
let detectionCounts = { person: 0, car: 0, motorcycle: 0, bus: 0 };
let realtimeChart = null;
let compositionChart = null;
let statsInterval = null;

document.addEventListener('DOMContentLoaded', function () {
    initializeMap();
    populateCCTVList();
    initializeSearch();
    if (currentCameraId !== null) {
        selectCCTV(currentCameraId);
    }
    initializeCharts();
    setInterval(updateDetectionDuration, 1000);
});

// KODE PERBAIKAN di dashboard.js
function switchTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(content => {
        content.style.display = 'none';
    });
    document.querySelectorAll('.tab-btn').forEach(button => {
        button.classList.remove('active-tab');
    });
    document.getElementById('tab-' + tabName).style.display = 'block';
    document.getElementById('btn-' + tabName).classList.add('active-tab');
    if (tabName === 'dashboard' && map) {
        setTimeout(() => map.invalidateSize(), 100);
    }
    // 👇 TAMBAHKAN KODE BLOK INI
    if (tabName === 'analysis') {
  setTimeout(() => {
    if (realtimeChart) {
      realtimeChart.resize();
      realtimeChart.update();
    }
    if (compositionChart) {
      compositionChart.resize();
      compositionChart.update();
    }
  }, 200);
}

}

function selectCCTV(cctvId) {
    const cctv = findCctvById(cctvId);
    if (!cctv) {
        console.error(`CCTV dengan ID ${cctvId} tidak ditemukan.`);
        return;
    }

    stopDetection();

    const loadingIndicator = document.getElementById('loading-detection');
    if (loadingIndicator) loadingIndicator.style.display = 'flex';
    
    currentCameraId = cctvId;

    document.getElementById('current-location').textContent = cctv.lokasi;
    document.querySelectorAll('.cctv-item').forEach(item => {
        item.classList.toggle('active', parseInt(item.dataset.cctvId) === cctvId);
    });

    if (cctv.latitude && cctv.longitude) {
        map.setView([cctv.latitude, cctv.longitude], 16);
        const markerData = markers.find(m => m.cctv.index === cctvId);
        if (markerData) markerData.marker.openPopup();
    }

    resetDetectionData();
    updateDetectionDisplay(cctv);

    if (cctv.status.toLowerCase() === 'aktif' && cctv.stream_url) {
        setTimeout(startDetection, 500);
    } else {
        if (loadingIndicator) loadingIndicator.style.display = 'none';
    }
}

function updateDetectionDisplay(cctv) {
    const detectionContainer = document.getElementById('detection-container');
    const loadingIndicator = document.getElementById('loading-detection');

    if (loadingIndicator) loadingIndicator.style.display = 'flex';

    if (cctv.status.toLowerCase() === 'aktif' && cctv.stream_url && cctv.stream_url.endsWith('.m3u8')) {
        detectionContainer.innerHTML = `
        <img
          id="detection-stream"
          src="/user/analyze_stream/${cctv.index}?_v=${new Date().getTime()}"
          alt="CCTV Detection Stream"
          class="w-full h-full object-contain"
          onload="document.getElementById('loading-detection').style.display = 'none';"
          onerror="handleStreamError()"
        />`;
    } else {
        detectionContainer.innerHTML = `
      <div class="flex flex-col items-center justify-center h-full">
        <img src="/static/img/cam_dead.svg" alt="CCTV Nonaktif" class="w-16 h-16 opacity-60 mb-2">
        <p class="text-gray-500">CCTV tidak aktif atau tidak mendukung deteksi</p>
      </div>`;
      if (loadingIndicator) loadingIndicator.style.display = 'none';
    }
}

function startDetection() {
    if (currentCameraId === null) return;
    
    isDetectionActive = true;
    detectionStartTime = new Date();
    detectionPaused = false;

    if (statsInterval) clearInterval(statsInterval);

    statsInterval = setInterval(() => fetchRealtimeStats(currentCameraId), 1000); 
    console.log(`Deteksi dimulai untuk CCTV ID: ${currentCameraId}`);
}

function stopDetection() {
    isDetectionActive = false;
    if (statsInterval) {
        clearInterval(statsInterval);
        statsInterval = null;
    }
    console.log('Deteksi dihentikan');
}

function fetchRealtimeStats(cctvId) {
    if (!isDetectionActive || detectionPaused) return;
    fetch(`/user/api/scan/${cctvId}/data`)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            return response.json();
        })
        .then(data => {
            if (data && data.detections) {
                detectionCounts = data.detections;
                updateCounterDisplay();
                updateDataCards();
                updateCharts();
            }
        })
        .catch(error => console.error('Error fetching realtime stats:', error));
}

function findCctvById(cctvId) {
    return cctvData.find(c => c.index === cctvId);
}

function populateCCTVList() {
    const listContainer = document.getElementById('cctv-list');
    if (!listContainer) return;
    listContainer.innerHTML = cctvData.map(cctv =>
        `<div class="cctv-item ${cctv.index === currentCameraId ? 'active' : ''}" 
          onclick="selectCCTV(${cctv.index})" 
          data-cctv-id="${cctv.index}">
      <div class="flex items-center justify-between">
        <div>
          <div class="flex items-center">
            <span class="status-indicator ${cctv.status.toLowerCase() === 'aktif' ? 'status-active' : 'status-inactive'}"></span>
            <span class="font-medium text-sm">${cctv.lokasi}</span>
          </div>
          <div class="text-xs text-gray-400 mt-1">${cctv.type} • ${cctv.status}</div>
        </div>
      </div>
    </div>`
    ).join('');
}

function initializeMap() {
    if(!document.getElementById('map')) return;
    map = L.map('map').setView([-6.5971, 106.8060], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
}).addTo(map);

    cctvData.forEach(cctv => {
        if (cctv.latitude && cctv.longitude) {
            const icon = L.divIcon({
                html: `<div style="background-color: ${cctv.status.toLowerCase() === 'aktif' ? '#10b981' : '#ef4444'};" class="w-3 h-3 rounded-full border-2 border-white shadow-md"></div>`,
                className: ''
            });
            
            const popupContent = document.createElement('div');
            popupContent.innerHTML = `<b>${cctv.lokasi}</b><br><small>Status: ${cctv.status}</small>`;

            const marker = L.marker([cctv.latitude, cctv.longitude], { icon }).addTo(map).bindPopup(popupContent);
            markers.push({ marker, cctv });
        }
    });
}

function initializeSearch() {
  const searchInput = document.getElementById('cctv-search');
  const searchResults = document.getElementById('search-results');
  if (!searchInput || !searchResults) return;

  searchInput.addEventListener('input', function (e) {
    const query = e.target.value.toLowerCase();
    if (query.length < 1) {
      searchResults.style.display = 'none';
      return;
    }
    const filtered = cctvData.filter(cctv => cctv.lokasi.toLowerCase().includes(query));
    if (filtered.length > 0) {
      searchResults.innerHTML = filtered.map(cctv => 
        `<div class="search-item" onclick="selectFromSearch(${cctv.index})">
            <div class="font-semibold">${cctv.lokasi}</div>
            <div class="text-sm text-gray-400">${cctv.type} - ${cctv.status}</div>
        </div>`
      ).join('');
      searchResults.style.display = 'block';
    } else {
      searchResults.innerHTML = '<div class="search-item text-gray-400">Tidak ada hasil ditemukan</div>';
      searchResults.style.display = 'block';
    }
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.search-container')) {
      searchResults.style.display = 'none';
    }
  });
}

function selectFromSearch(cctvId) {
  document.getElementById('search-results').style.display = 'none';
  document.getElementById('cctv-search').value = '';
  selectCCTV(cctvId);
}

// ============== FUNGSI UTILITAS & UPDATE UI ==============

function updateCounterDisplay() {
    document.getElementById('personCount').textContent = detectionCounts.person || 0;
    document.getElementById('carCount').textContent = detectionCounts.car || 0;
    document.getElementById('motorcycleCount').textContent = detectionCounts.motorcycle || 0;
    document.getElementById('busCount').textContent = detectionCounts.bus || 0;
}

function resetDetectionData() {
  detectionCounts = { person: 0, car: 0, motorcycle: 0, bus: 0 };
  detectionStartTime = new Date();
  updateCounterDisplay();
  updateDataCards();
  if (realtimeChart) {
    realtimeChart.data.labels = [];
    realtimeChart.data.datasets.forEach(dataset => dataset.data = []);
    realtimeChart.update();
  }
  if (compositionChart) {
    compositionChart.data.datasets[0].data = [0, 0, 0, 0];
    compositionChart.update();
  }
}

function updateDataCards() {
  const total = Object.values(detectionCounts).reduce((a, b) => a + b, 0);
  document.getElementById('total-detections').textContent = total;
  const maxType = Object.keys(detectionCounts).reduce((a, b) => (detectionCounts[a] || 0) > (detectionCounts[b] || 0) ? a : b, 'person');
  const typeNames = { person: 'Orang', car: 'Mobil', motorcycle: 'Motor', bus: 'Bus' };
  document.getElementById('most-detected').textContent = (detectionCounts[maxType] || 0) > 0 ? typeNames[maxType] : '-';
}

function updateDetectionDuration() {
  const durationEl = document.getElementById('detection-duration');
  if (!durationEl) return;
  if (!isDetectionActive || !detectionStartTime) {
    durationEl.textContent = '00:00';
    return;
  }
  const elapsed = Math.floor((new Date() - detectionStartTime) / 1000);
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  durationEl.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

function handleStreamError() {
  const detectionContainer = document.getElementById('detection-container');
  detectionContainer.innerHTML = `
    <div class="flex flex-col items-center justify-center h-full">
      <svg width="48" height="48" class="mb-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
      <p class="text-red-400 font-medium">Stream Terputus</p>
      <p class="text-gray-400 text-sm mt-2">Gagal memuat video stream CCTV.</p>
    </div>`;
}

function initializeCharts() {
  if (!document.getElementById('realtimeChart') || !document.getElementById('compositionChart')) return;
  const textColor = '#E5E7EB';
  const customColors = ['#E2562A', '#10DFB4', '#FACC15', '#8F363D'];
  
  const realtimeCtx = document.getElementById('realtimeChart').getContext('2d');
  realtimeChart = new Chart(realtimeCtx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Total Deteksi', data: [], borderColor: '#10DFB4', backgroundColor: 'rgba(16, 223, 180, 0.1)', tension: 0.4, fill: true }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: 'rgba(255, 255, 255, 0.1)' } }, x: { ticks: { color: textColor }, grid: { display: false } } } }
  });

  const compositionCtx = document.getElementById('compositionChart').getContext('2d');
  compositionChart = new Chart(compositionCtx, {
    type: 'doughnut',
    data: { labels: ['Orang', 'Mobil', 'Motor', 'Bus'], datasets: [{ data: [0, 0, 0, 0], backgroundColor: customColors, hoverOffset: 4, borderColor: '#141B35' }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: textColor } } } }
  });
}

function updateCharts() {
  if (!realtimeChart || !compositionChart) return;
  const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const total = Object.values(detectionCounts).reduce((a, b) => a + b, 0);
  
  realtimeChart.data.labels.push(now);
  realtimeChart.data.datasets[0].data.push(total);
  if (realtimeChart.data.labels.length > 15) {
    realtimeChart.data.labels.shift();
    realtimeChart.data.datasets[0].data.shift();
  }
  realtimeChart.update('none');

  compositionChart.data.datasets[0].data = [
    detectionCounts.person || 0,
    detectionCounts.car || 0,
    detectionCounts.motorcycle || 0,
    detectionCounts.bus || 0
  ];
  compositionChart.update('none');
}

// Event listener untuk tab browser
document.addEventListener('visibilitychange', function() {
  if (document.hidden) {
    // Kurangi frekuensi update saat tab tidak aktif untuk hemat resource
    if (statsInterval) {
      clearInterval(statsInterval);
      statsInterval = setInterval(() => fetchRealtimeStats(currentCameraId), 5000);
    }
  } else {
    // Kembalikan frekuensi update normal saat tab kembali aktif
    if (isDetectionActive && !detectionPaused) {
      clearInterval(statsInterval);
      statsInterval = setInterval(() => fetchRealtimeStats(currentCameraId), 1000);
    }
  }
});