// Menunggu sampai seluruh struktur halaman HTML (DOM) selesai dimuat.
document.addEventListener('DOMContentLoaded', () => {

    // =================================================================
    // 🌎 INITIALIZATION & MAP SETUP
    // =================================================================
    console.log("DOM siap, memulai inisialisasi peta...");

    const getJSONData = (id) => {
    const elem = document.getElementById(id);
    if (!elem || !elem.textContent) {
        return null;
    }
    const text = elem.textContent.trim();
    // Jika data adalah string 'null', kembalikan objek kosong atau null, bukan mencoba parse
    if (text === 'null' || text === '') {
        return null; 
    }
    try {
        return JSON.parse(text);
    } catch (e) {
        console.error(`Error parsing JSON dari #${id}:`, e, "Data:", text);
        // Jika parsing gagal (mis. string 'undefined' atau JSON rusak), kembalikan null
        return null; 
    }
}

    // 1. Ambil data dari HTML.
    const cctvMarkersData = getJSONData("cctv-data") || [];
    const batasKecamatanData = getJSONData("batas-data") || { features: [] };

    // 2. Inisialisasi Peta Leaflet
    const map = L.map("map", {
        zoomControl: false,
        center: [-6.595, 106.816],
        zoom: 12,
        preferCanvas: true
    });

    // 3. Tambahkan Tile Layer (gambar peta) dari OpenStreetMap
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Fix untuk rendering peta - trigger resize setelah load
    setTimeout(() => {
        map.invalidateSize();
    }, 100);

    // 4. Inisialisasi variabel global
    let allMarkers = [];
    let selectedKecamatan = "semua";


    // =================================================================
    // 🗺️ GEOJSON & COLOR MAPPING
    // =================================================================

    const kecamatanLayer = L.geoJSON(batasKecamatanData, {
        style: { color: "#444", weight: 1, fillOpacity: 0.15, fillColor: "#888" },
    }).bindPopup((layer) => `<b>${layer.feature.properties.name}</b>`).addTo(map);

    const WARNA_KECAMATAN = [
        "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
        "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff"
    ];
    const kecamatanColorMap = {};
    batasKecamatanData.features.forEach((area, index) => {
        kecamatanColorMap[area.properties.name] = WARNA_KECAMATAN[index % WARNA_KECAMATAN.length];
    });


    // =================================================================
    // 📍 MARKER INITIALIZATION
    // =================================================================

    cctvMarkersData.forEach((markerData) => {
        if (typeof markerData.latitude !== "number" || typeof markerData.longitude !== "number") return;

        let markerIcon;
        const tipe = markerData.type ? markerData.type.toLowerCase() : 'area publik';
        const isAktif = markerData.status?.toLowerCase() === 'aktif';

        if (['pasar', 'taman', 'tol'].includes(tipe)) {
            const statusSuffix = isAktif ? '_aktif' : '_tidak-aktif';
            markerIcon = L.icon({
                iconUrl: `/static/img/icon_${tipe}${statusSuffix}.png`,
                iconSize: [32, 32], iconAnchor: [16, 32], popupAnchor: [0, -32]
            });
        } else {
            markerIcon = L.divIcon({
                className: isAktif ? 'cctv-marker-active' : 'cctv-marker-nonaktif',
                iconSize: [15, 15], iconAnchor: [7.5, 7.5],
            });
        }

        const leafletMarker = L.marker([markerData.latitude, markerData.longitude], { icon: markerIcon })
            .bindPopup(markerData.popup_content, { maxWidth: "auto" });

        const containingLayers = leafletPip.pointInLayer(leafletMarker.getLatLng(), kecamatanLayer, true);
        const markerKecamatan = containingLayers.length > 0 ? containingLayers[0].feature.properties.name : null;

        allMarkers.push({ data: markerData, leafletMarker, kecamatan: markerKecamatan });
    });


    // =================================================================
    // 🛠️ HELPER & CORE FUNCTIONS
    // =================================================================

    function createCctvCardHTML(markerInfo) {
    const { data } = markerInfo;
    const isAktif = data.status?.toLowerCase() === "aktif";
    const hasVideo = isAktif && data.video_url;

    // --- PERUBAHAN DI SINI ---
    // Ganti placeholder gambar menjadi div dengan teks
    const feedContent = hasVideo
        ? `<iframe class="w-full h-full object-cover" data-src="${data.video_url}" src="" frameborder="0" allow="autoplay; encrypted-media" muted playsinline scrolling="no"></iframe>`
        : `<div class="w-full h-full flex flex-col items-center justify-center bg-gray-800 text-gray-500">
             <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.55a1 1 0 011.45.89V18a1 1 0 01-1.45.89L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
             <span class="text-xs font-semibold">Stream Tidak Tersedia</span>
           </div>`;
    // --- AKHIR PERUBAHAN ---

    return `
      <div class="bg-[#2d3748] rounded-lg overflow-hidden cursor-pointer text-white hover:bg-gray-600"
           onclick="zoomToMarker(${data.latitude}, ${data.longitude})">
        <div class="bg-black h-24 flex items-center justify-center relative overflow-hidden">
          ${feedContent}
        </div>
        <div class="p-2">
          <p class="font-semibold text-sm truncate">
            <span class="inline-block w-2 h-2 ${isAktif ? "bg-green-500" : "bg-red-500"} rounded-full mr-2"></span>
            ${data.lokasi || "Nama Lokasi"}
          </p>
        </div>
      </div>`;
}

    function updateMarkersVisibility() {
        const showAktif = document.getElementById("toggle-aktif").checked;
        const showNonaktif = document.getElementById("toggle-nonaktif").checked;
        const previewContainer = document.getElementById("cctv-preview-list");
        let allCardsHTML = "";

        allMarkers.forEach((markerInfo) => {
        const isAktif = markerInfo.data.status?.toLowerCase() === "aktif";
        const statusVisible = (showAktif && isAktif) || (showNonaktif && !isAktif);
        // Hapus atau abaikan filter kecamatan untuk debugging
        // const kecamatanVisible = selectedKecamatan === "semua" || markerInfo.kecamatan === selectedKecamatan; 
        const kecamatanVisible = true; // <--- SET INI KE TRUE UNTUK DEBUGGING

        if (statusVisible && kecamatanVisible) { // <--- HANYA GUNAKAN statusVisible
            markerInfo.leafletMarker.addTo(map);
            allCardsHTML += createCctvCardHTML(markerInfo);
        } else {
            map.removeLayer(markerInfo.leafletMarker);
        }
    });

        previewContainer.innerHTML = allCardsHTML;
        observeCctvCards();
    }

    function updateCctvCount() {
        const aktif = allMarkers.filter(m => m.data.status?.toLowerCase() === "aktif").length;
        const nonaktif = allMarkers.length - aktif;
        document.getElementById("cctv-active-count").textContent = aktif;
        document.getElementById("cctv-nonaktif-count").textContent = nonaktif;
    }

    function searchMarkers() {
        const keyword = document.getElementById("search-input").value.trim().toLowerCase();
        selectedKecamatan = "semua";
        document.getElementById("filter-kecamatan-label").innerText = "Filter Kecamatan";
        document.getElementById("toggle-aktif").checked = true;
        document.getElementById("toggle-nonaktif").checked = true;

        kecamatanLayer.eachLayer((layer) => {
            layer.setStyle({ color: "#444", weight: 1, fillOpacity: 0.15, fillColor: "#888" });
            if (!map.hasLayer(layer)) {
                map.addLayer(layer);
            }
        });
        map.fitBounds(kecamatanLayer.getBounds());

        if (!keyword) {
            updateMarkersVisibility();
            return;
        }

        const previewContainer = document.getElementById("cctv-preview-list");
        let allCardsHTML = "";
        let foundMarkers = [];

        allMarkers.forEach((markerInfo) => {
            const isMatch = markerInfo.data.lokasi?.toLowerCase().includes(keyword) || 
                          markerInfo.data.type?.toLowerCase().includes(keyword);
            if (isMatch) {
                foundMarkers.push(markerInfo);
                allCardsHTML += createCctvCardHTML(markerInfo);
                markerInfo.leafletMarker.addTo(map);
            } else {
                map.removeLayer(markerInfo.leafletMarker);
            }
        });

        previewContainer.innerHTML = allCardsHTML;
        observeCctvCards();

        if (foundMarkers.length > 0) {
            const group = new L.featureGroup(foundMarkers.map((m) => m.leafletMarker));
            map.fitBounds(group.getBounds().pad(0.2));
        }
    }

    // cctv.js

function pilihKecamatan(nama, isInit = false) {
    selectedKecamatan = nama;
    const labelElement = document.getElementById("filter-kecamatan-label");
    labelElement.innerText = nama === "semua" ? "Filter Kecamatan" : nama;

    // Definisikan style default dan style untuk layer yang 'tersembunyi'
    const visibleStyle = {
        color: "#444",
        weight: 1,
        fillOpacity: 0.15,
        fillColor: "#888",
    };
    const hiddenStyle = {
        opacity: 0,
        fillOpacity: 0,
    };

    let targetBounds = null;

    kecamatanLayer.eachLayer((layer) => {
        const layerName = layer.feature.properties.name;

        if (nama === "semua") {
            // Jika "semua", tampilkan semua layer dengan style default
            layer.setStyle(visibleStyle);
        } else {
            // Jika memilih kecamatan spesifik
            if (layerName === nama) {
                // Beri style highlight pada layer yang dipilih
                const highlightColor = kecamatanColorMap[nama] || "#facc15";
                layer.setStyle({
                    color: highlightColor,
                    weight: 2,
                    fillOpacity: 0.5,
                    fillColor: highlightColor,
                });
                targetBounds = layer.getBounds(); // Simpan bounds dari layer ini
            } else {
                // Sembunyikan layer lain dengan membuatnya transparan
                layer.setStyle(hiddenStyle);
            }
        }
    });

    // Atur zoom peta
    if (nama === "semua") {
        map.fitBounds(kecamatanLayer.getBounds());
    } else if (targetBounds) {
        map.fitBounds(targetBounds);
    }

    if (!isInit) {
        toggleFilter('kecamatan');
    }
    updateMarkersVisibility();
}

    function toggleFilter(type) {
        const statusDiv = document.getElementById("filter-status");
        const kecamatanDiv = document.getElementById("filter-kecamatan-content");
        const statusArrow = document.getElementById("status-arrow");
        const kecamatanArrow = document.getElementById("kecamatan-arrow");

        if (type === 'status') {
            const isHidden = statusDiv.classList.toggle("hidden");
            statusArrow.classList.toggle("rotate-180", !isHidden);
            // Tutup kecamatan
            kecamatanDiv.classList.add("hidden");
            kecamatanArrow.classList.remove("rotate-180");
        } else if (type === 'kecamatan') {
            const isHidden = kecamatanDiv.classList.toggle("hidden");
            kecamatanArrow.classList.toggle("rotate-180", !isHidden);
            // Tutup status
            statusDiv.classList.add("hidden");
            statusArrow.classList.remove("rotate-180");
        }
    }

    // Fungsi global untuk zoom ke marker (dipanggil dari onclick di HTML)
    window.zoomToMarker = function(lat, lng) {
        map.setView([lat, lng], 17);
        allMarkers.find(
            (m) => m.data.latitude === lat && m.data.longitude === lng
        )?.leafletMarker.openPopup();
    }


    // =================================================================
    // 🎥 VIDEO AUTOPLAY MANAGEMENT
    // =================================================================

    const activeVideos = new Set();
    const MAX_ACTIVE = 4;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            const iframe = entry.target;
            const videoId = iframe.dataset.id;

            if (entry.isIntersecting) {
                if (!activeVideos.has(videoId)) {
                    if (activeVideos.size >= MAX_ACTIVE) {
                        const firstId = activeVideos.values().next().value;
                        const firstIframe = document.querySelector(`iframe[data-id="${firstId}"]`);
                        if (firstIframe) firstIframe.src = "";
                        activeVideos.delete(firstId);
                    }
                    iframe.src = iframe.dataset.src;
                    activeVideos.add(videoId);
                }
            } else {
                if (activeVideos.has(videoId)) {
                    iframe.src = "";
                    activeVideos.delete(videoId);
                }
            }
        });
    }, { threshold: 0.5 });

    function observeCctvCards() {
        document.querySelectorAll("#cctv-preview-list iframe[data-src]").forEach((iframe, idx) => {
            iframe.dataset.id = idx;
            observer.observe(iframe);
        });
    }


    // =================================================================
    // 🖱️ UI INTERACTIONS & EVENT LISTENERS
    // =================================================================

    // Event listener untuk toggle filter status
    document.getElementById("toggle-status-button").addEventListener("click", () => {
        toggleFilter('status');
    });

    // Event listener untuk toggle filter kecamatan
    document.getElementById("toggle-kecamatan-button").addEventListener("click", () => {
        toggleFilter('kecamatan');
    });

    // Event listener untuk checkbox aktif/nonaktif
    document.getElementById("toggle-aktif").addEventListener("change", updateMarkersVisibility);
    document.getElementById("toggle-nonaktif").addEventListener("change", updateMarkersVisibility);

    // Event listener untuk search button
    document.getElementById("search-button").addEventListener("click", searchMarkers);

    // Event listener untuk Enter key di search input
    document.getElementById("search-input").addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            searchMarkers();
        }
    });

    // Event listener untuk setiap item kecamatan
    document.querySelectorAll("#kecamatan-list li").forEach((item) => {
        item.addEventListener("click", () => {
            const kecamatanName = item.getAttribute("data-kecamatan");
            pilihKecamatan(kecamatanName);
        });
    });


    // =================================================================
    // 🚀 INITIAL STATE SETUP
    // =================================================================

    updateCctvCount();
    updateMarkersVisibility();
    pilihKecamatan("semua", true);

    // Force map refresh untuk memastikan tiles ter-load dengan baik
    setTimeout(() => {
        map.invalidateSize();
        map.setView([-6.595, 106.816], 12);
    }, 250);

    // Tambahan: refresh map saat window di-resize
    window.addEventListener('resize', () => {
        map.invalidateSize();
    });

    console.log("Inisialisasi selesai. Peta seharusnya tampil.");
});