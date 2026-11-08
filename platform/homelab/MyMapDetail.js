var mosaicEditorReady = false;
var mosaicSavePending = false;
var mosaicUnavailableSelection = false;

function setMosaicEditorStatus(message, retry) {
    $('#mosaicLoadStatus').text(message || '');
    $('#mosaicLoadRetry').prop('hidden', !retry);
    updateMosaicSelectionState();
}

function saveMyMap() {
    if (!mosaicEditorReady || mosaicSavePending) return;
    var name = ($('#inputName').val() || '').trim();
    var selectedValues = [];
    $('input[name="items-selected"]:checked').each(function () { selectedValues.push($(this).val()); });
    if (!name || name.length > 250) {
        mostrarAlertaError('MYMOSAICS', 'Enter a Mosaic name of 1 to 250 characters.', 'BACK');
        $('#inputName').trigger('focus'); return;
    }
    if (!selectedValues.length) {
        mostrarAlertaError('MYMOSAICS', 'Select at least one MosaicAsset before saving.', 'BACK'); return;
    }
    if (mosaicUnavailableSelection && !window.confirm('Some previously selected assets are no longer available. Save with only the available selection?')) return;
    mosaicSavePending = true;
    updateMosaicSelectionState();
    $.ajax({
        type: 'POST', dataType: 'json', contentType: 'application/json; charset=utf-8',
        url: MYLIBRARY.getUrl_page() + '/saveMap',
        data: JSON.stringify({ name: name, selectedValues: selectedValues, map_id: $('#hdnid').val() || '0', visibility: $('#selectMyMapVisibility').val() || 'private' }),
        success: function (response) {
            var id = response && response.d;
            if (!id || !/^\d+$/.test(String(id)) || Number(id) <= 0) { mosaicSaveError(); return; }
            $('#hdnid').val(id);
            mostrarAlertaOK('Mosaic saved', 'Your Mosaic has been saved successfully.', 'DONE');
            setTimeout(function () { window.location.href = '/en/mymaps'; }, 350);
        },
        error: mosaicSaveError
    });
}

function mosaicSaveError() {
    mosaicSavePending = false;
    updateMosaicSelectionState();
    mostrarAlertaError('MYMOSAICS', 'The Mosaic could not be saved. Your selection is still here. Please try again.', 'BACK');
}

function loadItemsSelected(search) {
    var mapId = $('#hdnid').val();
    if (!(parseInt(mapId, 10) > 0)) {
        mosaicEditorReady = true; setMosaicEditorStatus('', false); return;
    }
    $.ajax({
        type: 'POST', dataType: 'json', contentType: 'application/json; charset=utf-8',
        url: MYLIBRARY.getUrl_page() + '/loadItemsSelected', data: JSON.stringify({ map_id: mapId, search: search }),
        success: function (response) {
            var selected;
            try { selected = JSON.parse(response.d); if (!Array.isArray(selected)) throw new Error(); }
            catch (invalid) { OnErrorCall(); return; }
            mosaicUnavailableSelection = false;
            selected.forEach(function (value) {
                var match = $('input[name="items-selected"]').filter(function () { return this.value === String(value); });
                if (!match.length) mosaicUnavailableSelection = true;
                match.prop('checked', true); activarBrillo(value);
            });
            mosaicEditorReady = true;
            setMosaicEditorStatus(mosaicUnavailableSelection ? 'Some saved assets are no longer available. Review your selection before saving.' : '', false);
        }, error: OnErrorCall
    });
}

function loadItems(search) {
    mosaicEditorReady = false;
    setMosaicEditorStatus('Loading your assets and saved selection…', false);
    $.ajax({
        type: 'POST', dataType: 'json', contentType: 'application/json; charset=utf-8',
        url: MYLIBRARY.getUrl_page() + '/loadItems', data: JSON.stringify({ search: search }),
        success: OnSuccessCall, error: OnErrorCall
    });
}

function OnSuccessCall(response) {
    var items;
    try { items = JSON.parse(response.d); if (!Array.isArray(items)) throw new Error(); }
    catch (invalid) { OnErrorCall(); return; }
    $('#items').html(items.map(tableTemplateData).join(''));
    MosaicExtensionFilter.populateFromCards('mosaicExtension', '.mosaic-select-wrap');
    loadItemsSelected(''); applyFilters(); updateMosaicSelectionState();
}

function mosaicEncode(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
}

function tableTemplateData(item) {
    var ID = item.ID, VALUE = item.VALUE == null ? String(item.ID) : String(item.VALUE), DOM_ID = item.DOM_ID || ('asset-' + item.ID), NAME = item.NAME, FILE_TYPE_ID = item.FILE_TYPE_ID, PROJECT_ID = item.PROJECT_ID;
    var AUTHOR = item.AUTHOR, IS_COLLECTION = item.IS_COLLECTION, IS_EXTERNAL = item.IS_EXTERNAL;
    var FORMAT = item.FORMAT, LATITUDE = item.LATITUDE, LONGITUDE = item.LONGITUDE;
    var namefolder = slugify(PROJECT_ID + '-' + NAME);
    NAME = mosaicEncode(NAME); AUTHOR = mosaicEncode(AUTHOR); FORMAT = mosaicEncode(FORMAT);
    var url = `${window.MOSAICEARTH_ASSET_BASE}${namefolder}/procesado/odm_orthophoto/odm_orthophoto.webp`;
    if (IS_EXTERNAL && LATITUDE != null && LONGITUDE != null) {
        var span = 0.012;
        url = `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=${LONGITUDE-span},${LATITUDE-span},${LONGITUDE+span},${LATITUDE+span}&bboxSR=4326&size=640,360&format=jpg&f=image`;
    }
    var icon = imagenTipo(FILE_TYPE_ID);

    var typeName = mosaicEncode(item.TYPE_LABEL || (FILE_TYPE_ID == 1 ? 'POINT CLOUD' : (FILE_TYPE_ID == 2 ? 'ORTHOPHOTO' : '3D MODEL')));
    var sourceKind = item.SOURCE || (IS_EXTERNAL ? 'imported' : 'processed');
    var download = FORMAT ? FORMAT : (FILE_TYPE_ID == 1 ? 'LAS · ZIP' : (FILE_TYPE_ID == 2 ? 'GEOTIFF · TIF' : 'OBJ · ZIP'));
    var detailsUrl = IS_EXTERNAL ? `/Client/ExternalAssetDetail.aspx?project_id=${PROJECT_ID}&asset_id=${Math.abs(Number(item.ASSET_ID || ID))}` : (item.IS_PRIVATE_RESULT ? `/Client/ProductDetail.aspx?project_id=${PROJECT_ID}&type=${FILE_TYPE_ID}` : `/en/ProductDetail?id=${ID}`);
    return `<div class="col-xl-3 col-lg-4 col-md-6 col-12 box-product-hide mosaic-select-wrap d-flex" data-ownership="${item.OWNERSHIP || (IS_COLLECTION == 1 ? 'acquired' : 'created')}" data-source="${sourceKind}" data-type="${FILE_TYPE_ID}" data-access="${item.ACCESS_FILTER || 'public'}" data-date="${item.DATE_VALUE || ''}" data-extensions="${mosaicEncode(item.EXTENSIONS)}">
        <article id="itembox-${DOM_ID}" class="mosaic-asset-card mosaic-card-v2 mosaic-select-card">
            <div class="mosaic-card-preview"><span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span><img class="mosaic-preview-image" src="${mosaicEncode(url)}" alt="" loading="lazy" decoding="async" onerror="this.hidden=true" />
                ${IS_EXTERNAL ? '<span class="preview-provenance">Location preview</span>' : ''}<span class="mosaic-card-type-icon" aria-label="${typeName}" title="${typeName}">${icon}</span>
                <span class="mosaic-card-origin ${sourceKind === 'processed' ? 'is-processed' : 'is-imported'}">${sourceKind === 'processed' ? 'PROCESSED' : 'IMPORTED'}</span>
                <a class="mosaic-card-details" href="${detailsUrl}" onclick="event.stopPropagation()">View details</a>
                <label class="mosaic-card-select" aria-label="Select ${NAME}">
                    <input id="item-${DOM_ID}" class="form-check-input" name="items-selected" value="${VALUE}" type="checkbox"/>
                    <span class="mosaic-select-indicator" aria-hidden="true">&#10003;</span>
                </label>
               <input type="hidden" class="item-is-collection" value="${IS_COLLECTION}" />
                <input type="hidden" class="item-file-type" value="${FILE_TYPE_ID}" />
            </div>
            <div class="mosaic-card-body">
                <h3 class="mosaic-card-title">${NAME}</h3>
                <p class="mosaic-card-creator">${AUTHOR || 'MosaicEarth creator'}</p>
                <p class="mosaic-card-date"><span>${item.DATE_LABEL || 'Created'}:</span><strong>${item.DATE_DISPLAY || '—'}</strong></p>
                <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>${download}</strong></span>${item.ACCESS ? `<span class="mosaic-access-state">${item.ACCESS}</span>` : ''}</div>
            </div>
        </article>
    </div>`;
}

var mosaicActiveFilters = { ownership: [], source: [], type: [], access: [] };

function mosaicGroupMatches(group, value) {
    return mosaicActiveFilters[group].length === 0 || mosaicActiveFilters[group].indexOf(String(value)) >= 0;
}

function updateMosaicFilterButtons() {
    var hasFilters = Object.keys(mosaicActiveFilters).some(function (group) { return mosaicActiveFilters[group].length > 0; });
    var allButton = document.querySelector('[data-mosaic-filter-action="all"]');
    if (allButton) {
        allButton.classList.toggle('active', !hasFilters);
        allButton.setAttribute('aria-pressed', hasFilters ? 'false' : 'true');
    }
    document.querySelectorAll('[data-mosaic-filter-group]').forEach(function (button) {
        var active = mosaicActiveFilters[button.dataset.mosaicFilterGroup].indexOf(button.dataset.mosaicFilterValue) >= 0;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

function applyFilters() {
    var searchInput = document.querySelector('[id$="txtSearch"]');
    var texto = ((searchInput && searchInput.value) || '').trim().toLowerCase();
    var fromDate = (document.getElementById('mosaicDateFrom') || {}).value || '';
    var toDate = (document.getElementById('mosaicDateTo') || {}).value || '';
    var visibleCount = 0;

    $(".box-product-hide").each(function () {
        var matchText = $(this).text().toLowerCase().indexOf(texto) !== -1;
        var matchGroups = mosaicGroupMatches('ownership', this.dataset.ownership) && mosaicGroupMatches('source', this.dataset.source) && mosaicGroupMatches('type', this.dataset.type) && mosaicGroupMatches('access', this.dataset.access);
        var matchDate = (!fromDate || (this.dataset.date && this.dataset.date >= fromDate)) && (!toDate || (this.dataset.date && this.dataset.date <= toDate));
        var extension = MosaicExtensionFilter.get('mosaicExtension');
        var isVisible = matchText && matchGroups && matchDate && MosaicExtensionFilter.matches(this.dataset.extensions, extension);
        if (isVisible) visibleCount++;
        this.classList.toggle('mosaic-filter-hidden', !isVisible);
        this.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
    });
    var counter = document.getElementById('mosaicFilterCount');
    if (counter) counter.textContent = visibleCount + (visibleCount === 1 ? ' asset' : ' assets');
}


function clearFilters() {
    var searchInput = document.querySelector('[id$="txtSearch"]');
    var fromDate = document.getElementById('mosaicDateFrom'), toDate = document.getElementById('mosaicDateTo');
    mosaicActiveFilters = { ownership: [], source: [], type: [], access: [] };
    if (searchInput) searchInput.value = '';
    if (fromDate) fromDate.value = '';
    if (toDate) toDate.value = '';
    var extension = document.getElementById('mosaicExtension');
    MosaicExtensionFilter.set(extension, '');
    updateMosaicFilterButtons();
    applyFilters();
}

function setMosaicFilter(button) {
    var group = button.dataset.mosaicFilterGroup, value = button.dataset.mosaicFilterValue;
    if (!group) return;
    var values = mosaicActiveFilters[group], index = values.indexOf(value);
    if (index >= 0) values.splice(index, 1); else values.push(value);
    updateMosaicFilterButtons();
    applyFilters();
}



function activarBrillo(ID) {
    var checkbox = $('input[name="items-selected"]').filter(function () { return this.value === String(ID); })[0];

    if (checkbox != null && checkbox.checked) {
        $(checkbox).closest('.mosaic-select-card').addClass("item-selected");
    } else {
        $(checkbox).closest('.mosaic-select-card').removeClass("item-selected");
    }
    updateMosaicSelectionState();
}

function updateMosaicSelectionState() {
    var selectedCount = $('input[name="items-selected"]:checked').length;
    var counter = document.getElementById('mosaicSelectionCount');
    if (counter) counter.textContent = selectedCount === 1 ? '1 MosaicAsset selected' : selectedCount + ' MosaicAssets selected';
    var saveButton = document.querySelector('[id$="btnCancel"]');
    if (saveButton) {
        saveButton.disabled = !mosaicEditorReady || mosaicSavePending;
        saveButton.textContent = mosaicSavePending ? 'SAVING…' : 'SAVE';
        saveButton.classList.toggle('has-selection', selectedCount > 0);
        saveButton.setAttribute('data-selection-count', String(selectedCount));
    }
}

function slugify(phrase) {
    let str = removeAccent(phrase).toLowerCase();
    str = str.replace(/[^a-z0-9\s-]/g, ''); // Remove all non valid chars          
    str = str.replace(/\s+/g, ' ').trim(); // convert multiple spaces into one space  
    str = str.replace(/\s/g, '-'); // Replace spaces by dashes
    return str;
}

function removeAccent(str) {
    return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}
function OnErrorCall(response) {
    mosaicEditorReady = false;
    setMosaicEditorStatus('Your assets or saved selection could not be loaded. Your saved Mosaic has not changed. Retry, or sign in again if your session expired.', true);
}

function filtrarTexto() {
    applyFilters();
}


function imagenTipo(tipo) {
    if (tipo == 1) {
        return `<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 12.527 13'>
        <g id='Grupo_3396' data-name='Grupo 3396' transform='translate(-227 -67)'>
            <circle id='Elipse_246' data-name='Elipse 246' cx='0.834' cy='0.834' r='0.834' transform='translate(227 78.331)' fill='#0e1411' />
            <circle id='Elipse_247' data-name='Elipse 247' cx='0.834' cy='0.834' r='0.834' transform='translate(229.175 78.331)' fill='#0e1411' />
            <circle id='Elipse_248' data-name='Elipse 248' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 78.331)' fill='#0e1411' />
            <circle id='Elipse_249' data-name='Elipse 249' cx='0.834' cy='0.834' r='0.834' transform='translate(227 76.042)' fill='#0e1411' />
            <circle id='Elipse_250' data-name='Elipse 250' cx='0.834' cy='0.834' r='0.834' transform='translate(229.175 76.042)' fill='#0e1411' />
            <circle id='Elipse_251' data-name='Elipse 251' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 76.042)' fill='#0e1411' />
            <circle id='Elipse_252' data-name='Elipse 252' cx='0.834' cy='0.834' r='0.834' transform='translate(227 73.752)' fill='#0e1411' />
            <circle id='Elipse_254' data-name='Elipse 254' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 73.752)' fill='#0e1411' />
            <circle id='Elipse_255' data-name='Elipse 255' cx='0.834' cy='0.834' r='0.834' transform='translate(233.509 78.331)' fill='#0e1411' />
            <circle id='Elipse_256' data-name='Elipse 256' cx='0.834' cy='0.834' r='0.834' transform='translate(235.683 78.331)' fill='#0e1411' />
            <circle id='Elipse_258' data-name='Elipse 258' cx='0.834' cy='0.834' r='0.834' transform='translate(233.509 76.042)' fill='#0e1411' />
            <circle id='Elipse_259' data-name='Elipse 259' cx='0.834' cy='0.834' r='0.834' transform='translate(235.683 76.042)' fill='#0e1411' />
            <circle id='Elipse_260' data-name='Elipse 260' cx='0.834' cy='0.834' r='0.834' transform='translate(237.858 76.042)' fill='#0e1411' />
            <circle id='Elipse_261' data-name='Elipse 261' cx='0.834' cy='0.834' r='0.834' transform='translate(233.509 73.752)' fill='#0e1411' />
            <circle id='Elipse_262' data-name='Elipse 262' cx='0.834' cy='0.834' r='0.834' transform='translate(235.683 73.752)' fill='#0e1411' />
            <circle id='Elipse_263' data-name='Elipse 263' cx='0.834' cy='0.834' r='0.834' transform='translate(237.858 73.752)' fill='#0e1411' />
            <circle id='Elipse_264' data-name='Elipse 264' cx='0.834' cy='0.834' r='0.834' transform='translate(227 71.579)' fill='#0e1411' />
            <circle id='Elipse_265' data-name='Elipse 265' cx='0.834' cy='0.834' r='0.834' transform='translate(229.175 71.579)' fill='#0e1411' />
            <circle id='Elipse_266' data-name='Elipse 266' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 71.579)' fill='#0e1411' />
            <circle id='Elipse_267' data-name='Elipse 267' cx='0.834' cy='0.834' r='0.834' transform='translate(227 69.289)' fill='#0e1411' />
            <circle id='Elipse_268' data-name='Elipse 268' cx='0.834' cy='0.834' r='0.834' transform='translate(229.175 69.289)' fill='#0e1411' />
            <circle id='Elipse_269' data-name='Elipse 269' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 69.289)' fill='#0e1411' />
            <circle id='Elipse_271' data-name='Elipse 271' cx='0.834' cy='0.834' r='0.834' transform='translate(229.175 67)' fill='#0e1411' />
            <circle id='Elipse_272' data-name='Elipse 272' cx='0.834' cy='0.834' r='0.834' transform='translate(231.35 67)' fill='#0e1411' />
            <circle id='Elipse_273' data-name='Elipse 273' cx='0.834' cy='0.834' r='0.834' transform='translate(233.508 71.579)' fill='#0e1411' />
            <circle id='Elipse_274' data-name='Elipse 274' cx='0.834' cy='0.834' r='0.834' transform='translate(235.683 71.579)' fill='#0e1411' />
            <circle id='Elipse_275' data-name='Elipse 275' cx='0.834' cy='0.834' r='0.834' transform='translate(237.858 71.579)' fill='#0e1411' />
            <circle id='Elipse_276' data-name='Elipse 276' cx='0.834' cy='0.834' r='0.834' transform='translate(233.508 69.289)' fill='#0e1411' />
            <circle id='Elipse_278' data-name='Elipse 278' cx='0.834' cy='0.834' r='0.834' transform='translate(237.858 69.289)' fill='#0e1411' />
            <circle id='Elipse_279' data-name='Elipse 279' cx='0.834' cy='0.834' r='0.834' transform='translate(233.508 67)' fill='#0e1411' />
            <circle id='Elipse_280' data-name='Elipse 280' cx='0.834' cy='0.834' r='0.834' transform='translate(235.683 67)' fill='#0e1411' />
            <circle id='Elipse_281' data-name='Elipse 281' cx='0.834' cy='0.834' r='0.834' transform='translate(237.858 67)' fill='#0e1411' />
        </g>
                                            </svg > `;
    } else if (tipo == 2) {
        return `<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 13 13'>
                                                <path id='Icon_open-grid-three-up' data-name='Icon open-grid-three-up' d='M0,0V3.25H3.25V0ZM4.875,0V3.25h3.25V0ZM9.75,0V3.25H13V0ZM0,4.875v3.25H3.25V4.875Zm4.875,0v3.25h3.25V4.875Zm4.875,0v3.25H13V4.875ZM0,9.75V13H3.25V9.75Zm4.875,0V13h3.25V9.75Zm4.875,0V13H13V9.75Z' />
                                            </svg>`;
    } else if (tipo == 3) {
        return `<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 12.068 13'>
                                                    <g id = 'Icon_ionic-ios-cube' data-name='Icon ionic-ios-cube' transform='translate(-3.375 -3.375)'>
                                                        <path id = 'Trazado_2605' data-name='Trazado 2605' d='M15.54,5.74l-5.2-2.33a.4.4,0,0,0-.328,0l-5.2,2.33a.244.244,0,0,0-.012.429L9.989,8.9a.4.4,0,0,0,.377,0l5.185-2.731A.244.244,0,0,0,15.54,5.74Z' transform='translate(-0.767 0)' fill='#000' />
                                                        <path id = 'Trazado_2606' data-name='Trazado 2606' d='M19.235,21.3l5.226-2.765a.234.234,0,0,0,.1-.2V12.04a.216.216,0,0,0-.31-.206l-5.226,2.693a.229.229,0,0,0-.122.206V21.1A.219.219,0,0,0,19.235,21.3Z' transform='translate(-9.122 -4.954)' fill='#000' />
                                                        <path id = 'Trazado_2607' data-name='Trazado 2607' d='M3.375,12.041v6.291a.234.234,0,0,0,.1.2l5.226,2.765a.214.214,0,0,0,.325-.2V14.734a.229.229,0,0,0-.122-.206L3.685,11.835A.217.217,0,0,0,3.375,12.041Z' transform='translate(0 -4.954)' fill='#000' />
                                                    </g>
                                                </svg>`;
    }
}

$(document).on('click', '.mosaic-select-card', function (event) {
    if ($(event.target).closest('a,input,label').length) return;
    var checkbox = $(this).find('input[name="items-selected"]')[0];
    if (!checkbox) return;
    checkbox.checked = !checkbox.checked;
    activarBrillo(checkbox.value);
});
$(document).on('change', 'input[name="items-selected"]', function () { activarBrillo(this.value); });
$(document).on('change', '#mosaicExtension', applyFilters);
$(document).on('click', '[data-mosaic-filter-group]', function () { setMosaicFilter(this); });
$(document).on('click', '[data-mosaic-filter-action="all"]', function () { mosaicActiveFilters = { ownership: [], source: [], type: [], access: [] }; updateMosaicFilterButtons(); applyFilters(); });
$(document).on('click', '#mosaicFilterReset', clearFilters);
$(document).on('change', '#mosaicDateFrom,#mosaicDateTo', applyFilters);
$(document).on('input', '[id$="txtSearch"]', applyFilters);

$(document).on('click', '#mosaicLoadRetry', function () { loadItems(''); });
