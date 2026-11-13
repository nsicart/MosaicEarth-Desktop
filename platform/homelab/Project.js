function subirfichero1() {

    $("#myFile1").click();

}

var orderBy = 0;
var camp = "";

var intervalID = setInterval(obtenerPorcentaje, 10000000000);
clearInterval(intervalID)

if ((($("#hdnEstado").val() == "2" || $("#hdnEstado").val() == "4") && $("#hdnEstado").val() != "4") || ($("#hdnEstado").val() == "4" && $("#hdnZip").val() == "0")) {
    ponerIntervalo();
}


$(".uploadbuttongcp").click(function () {
    $("#myFileGCP").click();
});

function getDocumentGCP(e) {
    
    let file = e.files[0]
    
    let filename = file.name;
    let filetype = file.type;
    let filesize = file.size;

    let data = {
        "filename": filename,
        "filetype": filetype,
        "filesize": filesize
    };
    let reader = new FileReader();
    reader.onload = function (e) {
        data.file_base64 = e.target.result.split(/,/)[1];
        console.log(data);
        $("#NombreDocumento1").val(data.filename);
        $("#Base641").val(data.file_base64);
        $("#FileName1").val(data.filename);
        $("#FileType1").val(data.filetype);
        $("#FileSize1").val(data.filesize);
        crearFileProject(filename, filesize, filetype);
    }
    reader.readAsDataURL(file)
}

function getFilesProject() {
    var url = MYLIBRARY.getUrl_page();

    var project_id = $("#hdnid").val();

    var data = { project_id };

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/loadFileProject",
        data: JSON.stringify(data),
        success: function (response) {

        },
        error: function (response) {

        }
    });
}

var dropzone = document.querySelector('.dropzone');
var input = document.getElementById('myFile1');

dropzone.ondragover = function (e) {
    e.preventDefault();
    $('.dropzone').addClass("dragover");
};
dropzone.ondragleave = function (e) {
    e.preventDefault();
    this.classList.remove('dragover');
};
dropzone.onclick = function (e) {
    if (e.target === input) return;
    e.preventDefault();
    $("#myFile1").click();
}
var totalfiles = 0;
var fechaActual = Date.now();
var uploadProgressByBatch = {};
var activePhotoUploadRequests = [];
var photoUploadCancelled = false;
var completedPhotoUploadBatches = 0;
var photoUploadController = null;
var photoUploadBatchBytes = {};
var totalPhotoUploadBytes = 0;
var skippedPhotoUploadFiles = 0;

function protectPhotoUploadNavigation() {
    window.onbeforeunload = function (event) {
        event.preventDefault();
        event.returnValue = '';
        return '';
    };
}

function releasePhotoUploadNavigation() {
    window.onbeforeunload = null;
}

function updateUploadVisual(batchIndex, loaded) {
    var batchBytes = photoUploadBatchBytes[batchIndex] || 0;
    uploadProgressByBatch[batchIndex] = Math.max(0, Math.min(Number(loaded) || 0, batchBytes));
    var uploadedBytes = 0;
    for (var key in uploadProgressByBatch) uploadedBytes += uploadProgressByBatch[key];
    var percent = Math.min(99, Math.floor((uploadedBytes / Math.max(totalPhotoUploadBytes, 1)) * 100));
    $("#porcentajefiles").text(percent + "%");
    $("#uploadLinearBar").css('width', percent + '%');
    $("#uploadStatusText").text('UPLOADING SOURCE IMAGERY…');
    var skippedText = skippedPhotoUploadFiles ? ' · ' + skippedPhotoUploadFiles + ' already stored' : '';
    $("#uploadBatchStatus").text(completedPhotoUploadBatches + ' of ' + totalfiles + ' batches securely stored' + skippedText);
}

function setPhotoUploadFailed(message) {
    releasePhotoUploadNavigation();
    $("#uploadStatusText").text('UPLOAD NEEDS ATTENTION');
    $(".source-transfer-warning").text(message || 'Some files could not be uploaded. Select the same files again to continue safely; images already stored will be skipped.');
    $("#cancelUploadFiles").text('CLOSE');
}

function setPhotoUploadPreparing() {
    uploadProgressByBatch = {};
    activePhotoUploadRequests = [];
    photoUploadCancelled = false;
    completedPhotoUploadBatches = 0;
    photoUploadController = null;
    photoUploadBatchBytes = {};
    totalPhotoUploadBytes = 0;
    skippedPhotoUploadFiles = 0;
    fechaActual = Date.now();
    $(".divaddfiles").css("display", "none");
    $(".porcentajeaddfiles").css("display", "block");
    $("#porcentajefiles").text('0%');
    $("#uploadLinearBar").css('width', '0%');
    $("#uploadStatusText").text('CHECKING SOURCE IMAGERY…');
    $("#uploadBatchStatus").text('Checking which files are already safely stored');
    $("#cancelUploadFiles").text('CANCEL UPLOAD');
}

function normalizeUploadFileName(name) {
    var clean = String(name || '').split(/[\\/]/).pop().trim();
    if (clean.normalize) clean = clean.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    clean = clean.replace(/[<>:"/\\|?*\x00-\x1F]/g, '_');
    var dot = clean.lastIndexOf('.');
    if (dot > -1) clean = clean.substring(0, dot) + clean.substring(dot).toLowerCase();
    return clean.toLowerCase();
}

function uploadFileIdentity(name, size) {
    return normalizeUploadFileName(name) + '|' + String(Number(size) || 0);
}

function getUploadStatus(projectId) {
    return $.ajax({
        type: 'POST',
        dataType: 'json',
        contentType: 'application/json; charset=utf-8',
        url: MYLIBRARY.getUrl_page() + '/GetSourceUploadStatus',
        data: JSON.stringify({ project_id: String(projectId) })
    });
}

function parseUploadError(response, fallback) {
    var payload = response && response.responseJSON;
    if (!payload && response && response.responseText) {
        try { payload = JSON.parse(response.responseText); } catch (ignore) { }
    }
    var status = response ? Number(response.status) || 0 : 0;
    var returnedHtml = response && /^\s*</.test(response.responseText || '');
    return {
        status: status,
        retriable: status === 0 || status === 408 || status === 429 || status >= 500,
        message: returnedHtml && status === 200
            ? 'Your session has expired. Sign in again before continuing the upload.'
            : (payload && payload.Message ? payload.Message : fallback)
    };
}

function uploadPhotoBatch(item) {
    return new Promise(function (resolve, reject) {
        var formData = new FormData();
        item.files.forEach(function (file, index) { formData.append('source_' + index, file, file.name); });
        formData.append('project_id', $("#hdnid").val());

        var request = $.ajax({
            type: 'POST',
            url: '/Client/Handler.ashx',
            data: formData,
            dataType: 'json',
            cache: false,
            contentType: false,
            processData: false,
            xhr: function () {
                var xhr = $.ajaxSettings.xhr();
                if (xhr.upload) xhr.upload.addEventListener('progress', function (event) {
                    if (event.lengthComputable) updateUploadVisual(item.index, event.loaded);
                });
                return xhr;
            },
            success: function (response) {
                if (response && response.Success === false) {
                    reject({ retriable: response.Retriable !== false, message: response.Message || 'This batch could not be stored.' });
                    return;
                }
                resolve(response || { Success: true });
            },
            error: function (response, status) {
                if (photoUploadCancelled || status === 'abort') {
                    reject({ retriable: false, cancelled: true, message: 'Upload cancelled.' });
                    return;
                }
                reject(parseUploadError(response, 'The connection was interrupted while uploading this batch.'));
            },
            complete: function () {
                var position = activePhotoUploadRequests.indexOf(request);
                if (position >= 0) activePhotoUploadRequests.splice(position, 1);
            }
        });
        activePhotoUploadRequests.push(request);
    });
}

function beginQueuedPhotoUpload(files, status) {
    if (photoUploadCancelled) return;
    var existing = {};
    (status.Files || []).forEach(function (file) { existing[uploadFileIdentity(file.Name, file.Size)] = true; });
    var pending = files.filter(function (file) {
        if (existing[uploadFileIdentity(file.name, file.size)]) {
            skippedPhotoUploadFiles++;
            return false;
        }
        return true;
    });

    var maxFiles = Number(status.MaxFilesPerProject) || 10000;
    if (Number(status.ExistingFileCount || 0) + pending.length > maxFiles) {
        setPhotoUploadFailed('This project accepts up to ' + maxFiles.toLocaleString() + ' source images. Remove some existing images or upload a smaller selection.');
        return;
    }
    var oversized = pending.filter(function (file) { return Number(file.size) > Number(status.MaxFileBytes || 536870912); });
    if (oversized.length) {
        setPhotoUploadFailed(oversized.length + ' selected image(s) exceed the per-file size limit.');
        return;
    }
    if (!pending.length) {
        $("#porcentajefiles").text('100%');
        $("#uploadLinearBar").css('width', '100%');
        $("#uploadStatusText").text('ALL SELECTED IMAGES ARE ALREADY UPLOADED');
        $("#uploadBatchStatus").text(skippedPhotoUploadFiles + ' files safely stored');
        $("#cancelUploadFiles").text('CLOSE');
        window.setTimeout(function () { location.reload(); }, 500);
        return;
    }

    var batches = MosaicPhotoUploadQueue.createBatches(pending, status.MaxBatchFiles || 5, status.MaxBatchBytes || 67108864);
    totalfiles = batches.length;
    batches.forEach(function (batch, index) {
        photoUploadBatchBytes[index] = batch.reduce(function (sum, file) { return sum + (Number(file.size) || 0); }, 0);
        totalPhotoUploadBytes += photoUploadBatchBytes[index];
    });
    $("#uploadStatusText").text('UPLOADING SOURCE IMAGERY…');
    $("#uploadBatchStatus").text('0 of ' + totalfiles + ' batches securely stored');
    protectPhotoUploadNavigation();

    photoUploadController = MosaicPhotoUploadQueue.run(batches, uploadPhotoBatch, {
        concurrency: status.MaxConcurrentBatches || 3,
        maxAttempts: status.MaxAttempts || 4,
        onState: function (event, item) {
            if (event === 'started' && item.attempt > 1) {
                uploadProgressByBatch[item.index] = 0;
                $("#uploadStatusText").text('RETRYING AN INTERRUPTED BATCH…');
            }
            if (event === 'completed') {
                completedPhotoUploadBatches++;
                updateUploadVisual(item.index, photoUploadBatchBytes[item.index]);
            }
        }
    });

    photoUploadController.promise.then(function (result) {
        if (result.cancelled || photoUploadCancelled) return;
        releasePhotoUploadNavigation();
        if (result.failures.length) {
            setPhotoUploadFailed(result.failures.length + ' batch(es) could not be uploaded after automatic retries. Select the same files again to continue safely.');
            return;
        }
        $("#porcentajefiles").text('100%');
        $("#uploadLinearBar").css('width', '100%');
        $("#uploadStatusText").text('UPLOAD COMPLETE');
        $("#uploadBatchStatus").text(completedPhotoUploadBatches + ' batches securely stored' + (skippedPhotoUploadFiles ? ' · ' + skippedPhotoUploadFiles + ' already present' : ''));
        $("#cancelUploadFiles").text('CLOSE');
        window.setTimeout(function () { location.reload(); }, 500);
    });
}

function startPhotoUpload(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    setPhotoUploadPreparing();
    if (files.some(function (file) { return !/\.(jpe?g|png|webp)$/i.test(file.name); })) {
        setPhotoUploadFailed('Select JPEG or PNG photos. Export HEIC, HEIF or TIFF images as JPEG or PNG before uploading. No files from this selection have been uploaded.');
        return;
    }
    getUploadStatus($("#hdnid").val()).done(function (response) {
        if (photoUploadCancelled) return;
        var status = response && response.d ? response.d : response;
        if (!status || status.Success !== true) {
            setPhotoUploadFailed(status && status.Message ? status.Message : 'The project could not be checked before uploading. Refresh the page and sign in again if necessary.');
            return;
        }
        beginQueuedPhotoUpload(files, status);
    }).fail(function (response) {
        if (photoUploadCancelled) return;
        var error = parseUploadError(response, 'The project could not be checked before uploading. Refresh the page and sign in again if necessary.');
        setPhotoUploadFailed(error.message);
    });
}

function cancelPhotoUpload() {
    photoUploadCancelled = true;
    if (photoUploadController) photoUploadController.cancel();
    activePhotoUploadRequests.forEach(function (request) {
        if (request && request.readyState !== 4) request.abort();
    });
    activePhotoUploadRequests = [];
    releasePhotoUploadNavigation();
    $("#uploadStatusText").text('UPLOAD CANCELLED');
    $("#uploadBatchStatus").text('Files already received remain available in the project.');
    $("#cancelUploadFiles").text('CLOSING…');
    window.setTimeout(function () { location.reload(); }, 250);
}
dropzone.ondrop = function (e) {
    e.preventDefault();
    this.classList.remove('dragover');
    startPhotoUpload(e.dataTransfer.files);
}

function getDocument1(e) {
    startPhotoUpload(e && e.files ? e.files : $("#myFile1").prop("files"));
}

function crearFileProject(filename, filesize, filetype) {

    var url = MYLIBRARY.getUrl_page();
    var filebase64 = $("#Base641").val();
    var project_id = $("#hdnid").val();


    var data = {
        project_id, filename, filesize, filetype, filebase64
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/btnCreateFileProject",
        data: JSON.stringify(data),
        success: function (response) {
            var x = response.d.split("/");
            $("#placeholdergcp").text("");
            $("#placeholdergcp").text(x[1]);
            $("#checkGCP").css("display", "inline");
            $(".uploadbuttongcp").css("display", "none");
            $(".deletebuttongcp").css("display", "block");
            $(".deletebuttongcp").attr('onclick', 'deleteGCP(' + x[0] + ')');
            $("#NombreDocumento1").val("");
            $("#Base641").val("");
            $("#FileName1").val("");
            $("#FileType1").val("");
            $("#FileSize1").val("");
        },
        error: function (response) {
            swal({
                type: 'error',
                title: 'something went wrong',
                text: 'ERROR',
                confirmButtonText: 'BACK',
            })
        }
    });
}

function deleteGCP(project_file_id) {
    var url = MYLIBRARY.getUrl_page();

    var data = {
        project_file_id
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/deleteGCP",
        data: JSON.stringify(data),
        success: function (response) {
            $("#placeholdergcp").text("Upload a geolocation file");
            $("#checkGCP").css("display", "none");
            $(".uploadbuttongcp").css("display", "block");
            $(".deletebuttongcp").css("display", "none");
        },
        error: function (response) {

        }
    });
}

function ponerIntervalo() {
    obtenerPorcentaje();
    intervalID = setInterval(obtenerPorcentaje, 10000);
}

function setProcessingStatus(message) {
    $("#porcentaje").text(message);
    $("#processingStageStatus").text(message);
}

function setProcessingWorkspaceVisible(visible, message) {
    var $panel = $("#processingStagePanel");
    $panel.toggleClass("processing-stage-panel--hidden", !visible);
    $("#countfiles, #cabecera").toggleClass("processing-files-muted", visible);

    if (visible) {
        $("#errorProjectState, #botonProcess, #botonAdd, #botonRetry").hide();
        $panel.closest(".content-box").addClass("project-is-processing");
        if (message) setProcessingStatus(message);
    } else {
        $panel.closest(".content-box").removeClass("project-is-processing");
    }
}

function restoreProjectAfterProcessingStartError() {
    setProcessingWorkspaceVisible(false);
    $("#botonProcess, #botonAdd").show();
    $("#processTask").prop("disabled", false);
}

function processTask1() {
    var url = MYLIBRARY.getUrl_page();
    var project_id = $("#hdnid").val();

    var data = {
        project_id
    }

    const truck_modal = document.querySelector('#staticBackdrop3');
    const modal = bootstrap.Modal.getInstance(truck_modal);
    modal.hide();
    $('body').removeClass('modal-open');
    $('.modal-backdrop').remove();

    $("#processTask").prop("disabled", true);
    $("#loading-screen").css("display", "none");
    setProcessingWorkspaceVisible(true, "STARTING PHOTOGRAMMETRY…");


    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/btnProcess",
        data: JSON.stringify(data),
        success: function (response) {
            var x = response.d; 
            $("#loading-screen").css("display", "none");
            if (x == "noGeolocalizado") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('ERROR', 'This project has no geolocation information', 'BACK');                
            } else if (x == "nocreditsadmin"){
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('ERROR', 'Something went wrong. Try again later.', 'BACK');    
            } else if (x == "nocreditsuser") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('ERROR', 'You have no available credits. Buy credits and try to process it again.', 'BACK');
            } else if (x == "odmnotconfigured") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('MANAGED PROCESSING UNAVAILABLE', 'The recovered local environment does not contain a configured MosaicEarth processing node. Your own node is optional and is not required when the platform service is available.', 'BACK');
            } else if (x.indexOf("managedodmconnection|") === 0) {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('MOSAICEARTH NODE UNAVAILABLE', 'The managed platform node is configured but could not be reached: ' + x.substring(21), 'BACK');
            } else if (x.indexOf("odmconnection|") === 0) {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('WEBODM CONNECTION FAILED', x.substring(14), 'BACK');
            } else if (x == "filesmissing") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('FILES NOT AVAILABLE', 'The database lists the images but their local source files are missing.', 'BACK');
            } else if (x == "nofiles" || x == "invalidproject") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('PROJECT NOT READY', 'This project cannot be processed yet.', 'BACK');
            } else if (x == "OK") {
                setProcessingWorkspaceVisible(true, "PREPARING PROCESSING…");
                $("#loading-screen").css("display", "none");
                clearInterval(intervalID);
                ponerIntervalo();
            } else if (x == "ERROR") {
                restoreProjectAfterProcessingStartError();
                mostrarAlertaError('ERROR', 'Something went wrong. Try again later.', 'BACK');
            }   
        },
        error: function (response) {
            $("#loading-screen").css("display", "none");
            restoreProjectAfterProcessingStartError();
            mostrarAlertaError('ERROR', 'Something went wrong. Try again later.', 'BACK');
        }
    });
}

function cancelTask() {
    var url = MYLIBRARY.getUrl_page();
    var project_id = $("#hdnid").val();

    var data = {
        project_id
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/btnCancelTask",
        data: JSON.stringify(data),
        success: function (response) {
            const truck_modal = document.querySelector('#staticBackdrop4');
            const modal = bootstrap.Modal.getInstance(truck_modal);
            modal.hide();
            $('body').removeClass('modal-open');
            $('.modal-backdrop').remove();
            $("#botonProcess").css("display", "inline-block");
            $("#botonAdd").css("display", "inline-block");
            $("#btnCancel").css("display", "none");
            $('.modal-backdrop').remove();
            clearInterval(intervalID);
            $("#porcentaje").text("");
            $("#imagen-spin").css("display", "none");
        },
        error: function (response) {
            
        }
    });
}

//gcp-file-box
//file-image-box
//active-upload
//gcp-file-src image-file-src

$("#gcp-file-box").click(function () {
    $("#file-image-box").removeClass("active-upload");
    $("#gcp-file-box").addClass("active-upload");
    $("#gcp-file-src").attr("src", "/Resources/file-gcp-active.svg");
    $("#image-file-src").attr("src", "/Resources/file-image.svg");
    $('#myFile1').attr("accept", ".txt");
});

$("#file-image-box").click(function () {
    $("#file-image-box").addClass("active-upload");
    $("#gcp-file-box").removeClass("active-upload");
    $("#gcp-file-src").attr("src", "/Resources/file-gcp.svg");
    $("#image-file-src").attr("src", "/Resources/file-image-active.svg");
    $('#myFile1').attr("accept", ".jpg,.jpeg,.png,.webp");
});


$("#cancelUploadFiles").click(function () {
    cancelPhotoUpload();
});

function obtenerPorcentaje() {
    var url = MYLIBRARY.getUrl_page();

    var project_id = $("#hdnid").val();

    $("#imagen-spin").css("display", "inline-block");

    var data = {
        project_id
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/obtenerProceso",
        data: JSON.stringify(data),
        success: function (response) {
            console.log(response.d);
            var x = response.d.split("/");            
            var activeProcessing = x[1] == "1" || x[1] == "2" || (x[1] == "4" && x[2] == "0");
            setProcessingWorkspaceVisible(activeProcessing);
            if (x[0] == "100") {
                setProcessingStatus("FINALIZING MOSAICASSETS…");
            } else if (x[0] == "0" && x[1] == "1") {
                 setProcessingStatus("QUEUED ON MOSAICEARTH NODE…");
            } else if (x[0] == "0") {
                 setProcessingStatus("PREPARING PROCESSING…");
            } else {
                setProcessingStatus(x[0] + "% PROCESSED");
            }
            if (x[1] == "4" && x[2] == "1") {
                window.location.href = window.__meTaskCompletedRedirectUrl || "/en/Projects";   
            }
            if (x[1] == "3") {
                mostrarAlertaError('ERROR', 'Something went wrong', 'BACK');
                $("#botonRetry").css("display", "inline-block");
                $("#botonAdd").css("display", "none");
                $("#btnCancel").css("display", "none");
                clearInterval(intervalID);
                setProcessingStatus("PROCESSING NEEDS ATTENTION");
                $("#imagen-spin").css("display", "none");
            }
        },
        error: function (response) {

        }
    });
}


function actualizarProgressBar() {
    $(".progress").each(function () {

        var value = $(this).attr('data-value');
        var left = $(this).find('.progress-left .progress-bar');
        var right = $(this).find('.progress-right .progress-bar');

        if (value > 0) {
            if (value <= 50) {
                right.css('transform', 'rotate(' + percentageToDegrees(value) + 'deg)')
            } else {
                right.css('transform', 'rotate(180deg)')
                left.css('transform', 'rotate(' + percentageToDegrees(value - 50) + 'deg)')
            }
        }

    })
}



    function percentageToDegrees(percentage) {

        return percentage / 100 * 360

    }




function mostrarAlertaErrorFiles(title, content, boton) {
    var prueba = "";
    swal({
        title: '<div style="background-color: #080808; padding: 10px 0px 10px 0px; text-align: center;">' + title + '</div>',
        color: '#716add',
        text: '<div class="row"><div class="col-12" style="font-size: 14px; font-family: OpenSansRegular; color: #BBBBBB; padding-top: 28px; padding-bottom: 45px;">' + content + '</div><div class="col-12" style="padding-bottom: 51px;"><img src="/Resources/KO.svg"/></div></div>',
        html: true,
        confirmButtonText: boton
    });
}

var publishWizardAssets = null;
var publishWizardState = { step: 1, placementIndex: 0, placementTypes: [], reviewed: {} };
function publishWizardEscape(value){return $('<div>').text(value==null?'':value).html();}
function processedCaptureIso(){var value=$('#inputCaptureDate').val()||'';var parts=value.split('/');return parts.length===3?parts[2]+'-'+parts[1]+'-'+parts[0]:/^\d{4}-\d{2}-\d{2}$/.test(value)?value:'';}
function initPublishWizard(){
 var publishedTypes=String($('#btnPublish').attr('data-published-types')||'').split(',').filter(Boolean);
 publishWizardAssets=[{type:'2',name:'Orthophoto',detail:'GeoTIFF and map viewer',review:''},{type:'3',name:'3D model',detail:'Height (Z) review required',review:$('#reviewRelocateModel3D').attr('href')||''},{type:'1',name:'Point cloud',detail:'Height (Z) review required',review:$('#reviewRelocatePointcloud').attr('href')||''}].map(function(asset){asset.published=publishedTypes.indexOf(asset.type)>=0;return asset;});
 publishWizardState={step:1,placementIndex:0,placementTypes:[],reviewed:{}};
 var description=$('#inputDescription').val()||'';
 $('#staticBackdrop7 .modal-body').html(`<div class="publish-wizard"><div class="publish-wizard-progress"><span class="active">1. Select</span><span>2. Position</span><span>3. Details</span><span>4. Review</span></div><section class="publish-wizard-step" data-wizard-step="1"><h3>Select MosaicAssets</h3><p>Choose only the useful results you want to make public and downloadable. Assets already published are locked and cannot be published twice. Additional Maps &amp; Layers have separate Review &amp; access actions on the project page.</p><div class="publish-asset-selector">${publishWizardAssets.map(a=>`<label class="${a.published?'is-published':''}"><input type="checkbox" name="publishAssetType" value="${a.type}" ${a.published?'disabled':'checked'}/><span>${a.name}</span><small>${a.published?'Already published':a.detail}</small></label>`).join('')}</div></section><section class="publish-wizard-step" data-wizard-step="2" hidden><h3>Review placement</h3><p>Every selected 3D result must pass through its real Cesium placement screen. Adjust height (Z), save it, and the assistant will continue.</p><div id="publishPlacementQueue"></div></section><section class="publish-wizard-step" data-wizard-step="3" hidden><h3>Information and access</h3><p>These details are shared by the selected results and appear on their public MosaicAsset pages.</p><label class="wizard-field">Description<textarea id="publishWizardDescription" rows="4" maxlength="1500">${publishWizardEscape(description)}</textarea></label><div class="wizard-temporal-fields"><label class="wizard-field">Captured from<input id="publishCaptureFrom" type="date" value="${processedCaptureIso()}"/><small>Detected from the source images when available. You can correct it.</small></label><label class="wizard-field">Captured until<input id="publishCaptureTo" type="date"/><small>Optional for assets acquired over a period.</small></label><label class="wizard-field">Date precision<select id="publishCapturePrecision"><option value="day">Exact day</option><option value="month">Month only</option><option value="year">Year only</option><option value="unknown">Unknown</option></select></label></div><label class="wizard-field">Optional price per MosaicAsset<div class="wizard-price"><input type="number" min="0" id="inputPublish" placeholder="FREE"/><span id="processedPriceUnit">FREE</span></div><small>Leave empty or enter 0 for free download. For paid acquisitions, 75% is added to your usage-credit balance.</small></label></section><section class="publish-wizard-step" data-wizard-step="4" hidden><h3>Ready to publish</h3><div id="publishWizardSummary"></div><p class="wizard-public-note">The selected MosaicAssets will be public immediately and remain available beyond private retention. Other users can acquire them under the license below. You cannot switch a published asset back to private yourself; contact support if publication needs correction. Moderation may follow.</p></section></div>`);
 $('[data-wizard-step="4"]').append('<div class="wizard-license"><span>LICENSE</span><strong>MosaicEarth Standard License v1.0</strong><p>Commercial use · Modifications allowed · No standalone redistribution</p><a href="/License.aspx" target="_blank" rel="noopener">View full license</a><label><input type="checkbox" id="publishRightsConfirmed"/>I confirm that I own or control all rights necessary to publish these assets and license them under the MosaicEarth Standard License.</label><small>Third-party rights and legally applicable source obligations still apply.</small></div>');
 $('#staticBackdrop7 .modal-footer').html('<button type="button" class="btn btn-cancel" id="publishWizardBack">CANCEL</button><button type="button" class="btn btn-primary" id="publishWizardNext">CONTINUE</button>');renderPublishWizard();
}
function renderPublishWizard(){$('[data-wizard-step]').prop('hidden',true).filter('[data-wizard-step="'+publishWizardState.step+'"]').prop('hidden',false);$('.publish-wizard-progress span').removeClass('active complete').each(function(i){if(i+1<publishWizardState.step)$(this).addClass('complete');if(i+1===publishWizardState.step)$(this).addClass('active');});$('#publishWizardBack').text(publishWizardState.step===1?'CANCEL':'BACK');$('#publishWizardNext').text(publishWizardState.step===4?'PUBLISH ASSETS':'CONTINUE').prop('disabled',publishWizardState.step===2&&publishWizardState.placementTypes.length&&Object.keys(publishWizardState.reviewed).length<publishWizardState.placementTypes.length);}
function startPlacementReview(){var selected=$('input[name=publishAssetType]:checked').map(function(){return this.value;}).get();publishWizardState.placementTypes=publishWizardAssets.filter(a=>selected.indexOf(a.type)>=0&&a.review);publishWizardState.placementIndex=0;publishWizardState.reviewed={};if(!publishWizardState.placementTypes.length){publishWizardState.step=3;renderPublishWizard();return;}renderPublishWizard();showPlacementAsset();}
function showPlacementAsset(){var asset=publishWizardState.placementTypes[publishWizardState.placementIndex];if(!asset){$('#publishPlacementQueue').html('<div class="wizard-placement-complete"><strong>Placement review complete</strong><span>All selected 3D results have a saved Z position.</span></div>');renderPublishWizard();return;}var separator=asset.review.indexOf('?')>=0?'&':'?';$('#publishPlacementQueue').html(`<div class="wizard-placement-head"><strong>${asset.name}</strong><span>${publishWizardState.placementIndex+1} of ${publishWizardState.placementTypes.length}</span></div><iframe class="wizard-placement-frame" title="Review ${asset.name} height" src="${asset.review}${separator}embed=1&publishWizard=1"></iframe><p class="wizard-placement-help">Use <strong>SAVE CHANGES</strong> inside the viewer. The next selected 3D result will open automatically.</p>`);}
window.addEventListener('message',function(event){if(event.origin!==window.location.origin||!event.data||event.data.type!=='mosaicearth-placement-saved')return;var asset=publishWizardState.placementTypes[publishWizardState.placementIndex];if(!asset||asset.type!==String(event.data.assetType))return;publishWizardState.reviewed[asset.type]=true;publishWizardState.placementIndex+=1;showPlacementAsset();});
$(document).on('shown.bs.modal','#staticBackdrop7',initPublishWizard);
$(document).on('click','#publishWizardBack',function(){if(publishWizardState.step===1){bootstrap.Modal.getInstance(document.getElementById('staticBackdrop7')).hide();return;}publishWizardState.step-=1;renderPublishWizard();});
$(document).on('click','#publishWizardNext',function(){if(publishWizardState.step===1){if(!$('input[name=publishAssetType]:checked').length){ShowMessageError('Select at least one MosaicAsset.','Publish MosaicAssets');return;}publishWizardState.step=2;renderPublishWizard();startPlacementReview();return;}if(publishWizardState.step===2){if(Object.keys(publishWizardState.reviewed).length<publishWizardState.placementTypes.length)return;publishWizardState.step=3;renderPublishWizard();return;}if(publishWizardState.step===3){publishWizardState.step=4;var names=$('input[name=publishAssetType]:checked').map(function(){var type=this.value;return publishWizardAssets.find(a=>a.type===type).name;}).get();var price=parseInt($('#inputPublish').val(),10)||0;$('#publishWizardSummary').html('<strong>'+names.join(', ')+'</strong><span>'+(price>0?price+' credits each':'Free download')+'</span>');renderPublishWizard();return;}requestPublish();});

var publicationPending = false;
function requestPublish() {
    if (publicationPending) return;
    var url = MYLIBRARY.getUrl_page();
    var project_id = $("#hdnid").val();
    var credits = $("#inputPublish").val();
    var numericCredits = parseInt(credits, 10);
    if (isNaN(numericCredits) || numericCredits < 0) numericCredits = 0;
    credits = numericCredits;
    var publish_type = numericCredits > 0 ? "0" : "1";
    var asset_types = $('input[name=publishAssetType]:checked').map(function () { return this.value; }).get().join(',');

    if (!asset_types) {
        ShowMessageError("Select at least one MosaicAsset to publish.", "Publish MosaicAssets");
        return;
    }
    if (!$('#publishRightsConfirmed').is(':checked')) {
        ShowMessageError('Confirm that you own or control the rights necessary to publish the selected MosaicAssets.', 'Publication rights');
        return;
    }

    $("#publishWizardNext").prop("disabled", true);
    $("#inputDescription").val($("#publishWizardDescription").val() || "");
    publicationPending = true;

    var data = {
        project_id, credits, publish_type, asset_types,
        capture_from: $("#publishCaptureFrom").val() || "",
        capture_to: $("#publishCaptureTo").val() || "",
        capture_precision: $("#publishCapturePrecision").val() || "unknown",
        rights_confirmed: true
    }

    // Publication starts only after the metadata write has succeeded.
    $.ajax({
        type: 'POST', dataType: 'json', contentType: 'application/json; charset=utf-8',
        url: url + '/saveChangesProject',
        data: JSON.stringify({ project_id: project_id, description: $('#inputDescription').val(), categoryId: $('#inputCategories').val() || '0', tags: $('#inputTags').val() || '' }),
        success: function () {
    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/requestPublish",
        data: JSON.stringify(data),
        success: function (response) {
            const truck_modal = document.querySelector('#staticBackdrop7');
            const modal = bootstrap.Modal.getInstance(truck_modal);
            modal.hide();
            $('body').removeClass('modal-open');
            $('.modal-backdrop').remove();
            

                var result={};try{result=JSON.parse(response.d||'{}');}catch(ignore){}
                $('#btnPublish').attr('data-published-types',(result.publishedTypes||[]).join(','));
                window.location.reload();
            
        },
        error: function () { publicationPending = false; $("#publishWizardNext").prop("disabled", false); ShowMessageError("The MosaicAssets could not be published. Try again.", "Publication error"); }
    });
        },
        error: function () {
            publicationPending = false;
            $('#publishWizardNext').prop('disabled', false);
            ShowMessageError('Your details could not be saved. Nothing has been published. Check your connection and try again.', 'Publication error');
        }
    });
}

function saveChanges() {
    var url = MYLIBRARY.getUrl_page();
    var project_id = $("#hdnid").val();
    var description = $("#inputDescription").val();
    var categoryId = $('#inputCategories').val();
    var tags = $('#inputTags').val();;
    var data = {
        project_id, description, categoryId, tags
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/saveChangesProject",
        data: JSON.stringify(data),
        success: function (response) {
            mostrarAlertaOK('Changes saved', 'Your changes have been saved successfully.', 'BACK');
        },
        error: function (response) {
            ShowMessageError('Your changes could not be saved. Keep this page open and try again.', 'Save error');
        }
    });
}

function Ordenacion(a, e) {
    restoreFilter();
    if (a % 2 == 0) {
        a--;

        $("#" + e[0].id).removeClass("sort").removeClass("sort-a").addClass("sort-d");
    }
    else {
        a++;
        $("#" + e[0].id).removeClass("sort").removeClass("sort-d").addClass("sort-a");
    }

    $("#" + e[0].id).attr("onclick", "filterOrder(" + a + ",$(this))");
}

function restoreFilter() {
    $(".sort-a").each(function (i, x) {
        $("#" + x.id).attr("onclick", "filterOrder(" + x.name + ",$(this))");
        $("#" + x.id).addClass("sort").removeClass("sort-d").removeClass("sort-a");
    });
    $(".sort-d").each(function (i, x) {
        $("#" + x.id).attr("onclick", "filterOrder(" + x.name + ",$(this))");
        $("#" + x.id).addClass("sort").removeClass("sort-d").removeClass("sort-a");
    });
}

function filterOrder(orden, campo) {
    var project_id = $("#hdnid").val();
    orderBy = orden;
    var url = MYLIBRARY.getUrl_page();
    camp = campo;

    var data = {
        project_id, orderBy
    };

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/filterProjectList",
        data: JSON.stringify(data),
        success: OnSuccessCall,
        error: OnErrorCall
    });

    Ordenacion(orden, campo);

}


function OnSuccessCall(response) {
    var json = $.parseJSON(response.d);
    $("#countfiles").empty();
    var filas = json;
    if (filas.length > 0) {
        for (i = 0; i < filas.length; i++) {
            $("#countfiles").append(tableTemplateData(filas[i]["PROJECT_FILE_ID"], filas[i]["FILENAME"], filas[i]["SIZE"], filas[i]["CAPTURE_DATE"]));
        }
    }
}

function tableTemplateData(ID, FILENAME, SIZE, CAPTURE_DATE) {
    var tipo = $('#hdnid').val();
    var megas = bytesToMegabytes(SIZE);
    var fecha = "--/--/-- --:--";
    var geolocation = "No";
    if (CAPTURE_DATE != null) {
        fecha = formatDateTime(CAPTURE_DATE);
        geolocation = "Yes";
    }
    
    return `<div class="row row-project showhim">
        <div class="col-md-3 header-table-project">
            ${FILENAME}
        </div>
        <div class="col-md-2 header-table-project">
            Image
        </div>
        <div class="col-md-2 header-table-project">
            ${megas} MB
        </div>
        <div class="col-md-2 header-table-project">
            ${fecha}
        </div>
        <div class="col-md-2 header-table-project">
            ${geolocation}
        </div>
        <div class="col-md-1 showme">
            <img runat="server" src="/Resources/basura.svg" onclick="deleteFileProject(${ID})" class="boton-delete" />
        </div>
    </div>`;
}

function convertirFormatoFecha(fechaStr) {
    var fecha = new Date(fechaStr);

    var dia = fecha.getDate();
    var mes = fecha.getMonth() + 1;
    var anio = fecha.getFullYear();

    var horas = fecha.getHours();
    var minutos = fecha.getMinutes();

    // Asegurarse de que los valores de día y mes tengan dos dígitos
    if (dia < 10) {
        dia = '0' + dia;
    }
    if (mes < 10) {
        mes = '0' + mes;
    }

    var formatoFecha = dia + '/' + mes + '/' + anio + ' ' + horas + ':' + minutos;

    return formatoFecha;
}

function OnErrorCall(response) {
    console.log("error");
}

function bytesToMegabytes(bytes) {
    const megabytes = bytes / (1024 * 1024);
    return megabytes.toFixed(2);
}

function formatDateTime(dateTimeStr) {
    const dateTime = new Date(dateTimeStr);
    const day = dateTime.getDate().toString().padStart(2, '0');
    const month = (dateTime.getMonth() + 1).toString().padStart(2, '0');
    const year = dateTime.getFullYear().toString();
    const hours = dateTime.getHours().toString().padStart(2, '0');
    const minutes = dateTime.getMinutes().toString().padStart(2, '0');
    return `${day}/${month}/${year} ${hours}:${minutes}`;
}

function deleteFileProject(id_project_file) {
    var project_id = $("#hdnid").val();

    var url = MYLIBRARY.getUrl_page();

    var data = {
        project_id, id_project_file
    };

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/deleteFile",
        data: JSON.stringify(data),
        success: function (response) {
            var json = $.parseJSON(response.d);
            $("#numberFiles").text(json["numberFiles"]);
            $("#numberCredits").text(json["creditsCost"]);
            $("#numMegabytes").text(json["sizesTotal"].toFixed(2));         
            $("#hdnAvailableCredits").text(json["userCredits"]);

            if (json["creditsCost"] > json["userCredits"]) {
                ProcessCreditsFalse();
            } else {
                ProcessCreditsTrue();
            }

            if (json["numberFiles"] == 0) {
                //$("#nofiles").css("display", "block");
                //$("#countfiles").css("display", "none");
                //$("#botonProcess").prop("disabled", false);
                location.reload();
            }

            filterOrder(orderBy, camp);
                    
        },
        error: function (response) {

        }
    });

}

function ProcessCreditsTrue() {
    var numberFiles = $("#numberFiles").text();
    var numberCredits = $("#numberCredits").text();
    var availableCredits = $("#hdnAvailableCredits").val();

    $("#numberFilestoProcess").text(numberFiles);
    $("#creditsCost").text(numberCredits);
    $("#availableCredits").text(availableCredits);

    $("#textAvailable").css("display", "inline");
    $("#textNoAvailable").css("display", "none");
    $("#processTask").prop("disabled", false);
}

function ProcessCreditsFalse() {
    var numberFiles = $("#numberFiles").text();
    var numberCredits = $("#numberCredits").text();
    var availableCredits = $("#hdnAvailableCredits").val();

    $("#numberFilestoProcess").text(numberFiles);
    $("#creditsCost").text(numberCredits);
    $("#availableCredits").text(availableCredits);

    $("#textAvailable").css("display", "none");
    $("#textNoAvailable").css("display", "inline");

    $("#processTask").prop("disabled", true);
}

function confirmUpgradeProject() {
    var url = MYLIBRARY.getUrl_page();
    var project = $("#hdnid").val();

    var data = {
        project
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/upgradeProjectToPremium",
        data: JSON.stringify(data),
        success: function (response) {
            if (response.d == "OK") {
                const truck_modal = document.querySelector('#modalNewPremiumProject');
                const modal = bootstrap.Modal.getInstance(truck_modal);
                modal.hide();
                $('.modal-backdrop').remove();
                $('body').removeClass('modal-open');
                mostrarAlertaOKrefresh('Changes saved', 'Your changes have been saved successfully.', 'BACK');
            } else if (response.d == "NO_FOUNDS") {
                mostrarAlertaError('Without Founds', 'This user dont have enough founds to buy this item.', 'BACK');
            }
        },
        error: function (response) {
            mostrarAlertaError('ERROR', 'An error happened during the purchase.', 'BACK');
        }
    });
}

function goToPremiumStep2() {
    $("#bodyPremiumStep1").css("display", "none");
    $("#bodyPremiumStep2").css("display", "block");

    $("#footerPremiumStep1").css("display", "none");
    $("#footerPremiumStep2").css("display", "block");
}

function goToPremiumStep1() {
    $("#bodyPremiumStep1").css("display", "block");
    $("#bodyPremiumStep2").css("display", "none");

    $("#footerPremiumStep1").css("display", "block");
    $("#footerPremiumStep2").css("display", "none");
}

function cancelPremiumProject() {
    var url = MYLIBRARY.getUrl_page();
    var project = $('#hdnid').val();

    var data = {
        project
    }

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/cancelProjectPremium",
        data: JSON.stringify(data),
        success: function (response) {
            if (response.d == "OK") {
                const truck_modal = document.querySelector('#modalPremiumProject');
                const modal = bootstrap.Modal.getInstance(truck_modal);
                modal.hide();
                $('.modal-backdrop').remove();
                $('body').removeClass('modal-open');
                mostrarAlertaOKrefresh('Changes saved', 'Your changes have been saved successfully.', 'BACK');
            } else {
                mostrarAlertaError('ERROR', 'An error happened with the cancel subscription.', 'BACK');
            }
        },
        error: function (response) {
            mostrarAlertaError('ERROR', 'An error happened with the cancel subscription.', 'BACK');
        }
    });
}

function claim() {
    var project_id = $("#hdnid").val();

    var url = MYLIBRARY.getUrl_page();

    var data = {
        project_id
    };

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/claim",
        data: JSON.stringify(data),
        success: function (response) {
            mostrarAlertaOK('Claim succesfuly', 'Your claim have been sent successfully to the Administrator and the Partner.', 'BACK');
        },
        error: function (response) {

        }
    });
}

function claimProccess() {
    var project_id = $("#hdnid").val();

    var url = MYLIBRARY.getUrl_page();

    var data = {
        project_id
    };

    $.ajax({
        type: "POST",
        dataType: 'json',
        contentType: "application/json; charset=utf-8",
        url: url + "/claimProccess",
        data: JSON.stringify(data),
        success: function (response) {
            mostrarAlertaOK('Claim succesfuly', 'Your claim have been sent successfully to the Administrator.', 'BACK');
        },
        error: function (response) {

        }
    });
}

function showOrthophoto() {
    $("#orthoShow").css("display", "none");
    $("#orthoPreview").css("display", "block");
}

function showPointcloud() {
    $("#pointcloudShow").css("display", "none");
    $("#pointcloudPreview").css("display", "block");
}

function show3d() {
    $("#show3d").css("display", "none");
    $("#3dpreview").css("display", "block");
}

$(document).on("input", "#inputPublish", function () {
    var value = parseInt(this.value, 10);
    $("#processedPriceUnit").text(!value || value < 1 ? "FREE" : "credits");
});

$(function () {
    var state = $("#hdnEstado").val(), zip = $("#hdnZip").val();
    var isWorking = state === "1" || state === "2" || (state === "4" && zip !== "1");
    setProcessingWorkspaceVisible(isWorking);
});
