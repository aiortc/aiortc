var pc = new RTCPeerConnection();

// get DOM elements
var dataChannelLog = document.getElementById('data-channel'),
    iceConnectionLog = document.getElementById('ice-connection-state'),
    iceGatheringLog = document.getElementById('ice-gathering-state'),
    signalingLog = document.getElementById('signaling-state');

// register some listeners to help debugging
pc.addEventListener('icegatheringstatechange', function() {
    iceGatheringLog.textContent += ' -> ' + pc.iceGatheringState;
}, false);
iceGatheringLog.textContent = pc.iceGatheringState;

pc.addEventListener('iceconnectionstatechange', function() {
    iceConnectionLog.textContent += ' -> ' + pc.iceConnectionState;
}, false);
iceConnectionLog.textContent = pc.iceConnectionState;

pc.addEventListener('signalingstatechange', function() {
    signalingLog.textContent += ' -> ' + pc.signalingState;
}, false);
signalingLog.textContent = pc.signalingState;

// connect audio / video
pc.addEventListener('track', function(evt) {
    if (evt.track.kind == 'video')
        document.getElementById('video').srcObject = evt.streams[0];
    else
        document.getElementById('audio').srcObject = evt.streams[0];
});

// data channel
var dc = null, dcInterval = null;

function negotiate() {
    return pc.createOffer().then(function(offer) {
        return pc.setLocalDescription(offer);
    }).then(function() {
        // wait for ICE gathering to complete
        return new Promise(function(resolve) {
            if (pc.iceGatheringState === 'complete') {
                resolve();
            } else {
                function checkState() {
                    if (pc.iceGatheringState === 'complete') {
                        pc.removeEventListener('icegatheringstatechange', checkState);
                        resolve();
                    }
                }
                pc.addEventListener('icegatheringstatechange', checkState);
            }
        });
    }).then(function() {
        var offer = pc.localDescription;
        var codec = document.getElementById('video-codec').value;
        if (codec !== 'default') {
            offer.sdp = sdpFilterCodec(codec, offer.sdp);
        }

        document.getElementById('offer-sdp').textContent = offer.sdp;
        return fetch('/offer', {
            body: JSON.stringify({
                sdp: offer.sdp,
                type: offer.type,
                video_transform: document.getElementById('video-transform').value
            }),
            headers: {
                'Content-Type': 'application/json'
            },
            method: 'POST'
        });
    }).then(function(response) {
        return response.json();
    }).then(function(answer) {
        document.getElementById('answer-sdp').textContent = answer.sdp;
        return pc.setRemoteDescription(answer);
    }).catch(function(e) {
        alert(e);
    });
}

function start() {
    document.getElementById('start').style.display = 'none';

    if (document.getElementById('use-datachannel').checked) {
        dc = pc.createDataChannel('chat');
        dc.onclose = function() {
            clearInterval(dcInterval);
            dataChannelLog.textContent += '- close\n';
        };
        dc.onopen = function() {
            dataChannelLog.textContent += '- open\n';
            dcInterval = setInterval(function() {
                var message = 'ping';
                dataChannelLog.textContent += '> ' + message + '\n';
                dc.send(message);
            }, 1000);
        };
        dc.onmessage = function(evt) {
            dataChannelLog.textContent += '< ' + evt.data + '\n';
        };
    }

    var constraints = {
        audio: document.getElementById('use-audio').checked,
        video: false
    };

    if (document.getElementById('use-video').checked) {
        var resolution = document.getElementById('video-resolution').value;
        if (resolution) {
            resolution = resolution.split('x');
            constraints.video = {
                width: parseInt(resolution[0], 0),
                height: parseInt(resolution[1], 0)
            };
        } else {
            constraints.video = true;
        }
    }

    if (constraints.audio || constraints.video) {
        if (constraints.video) {
            document.getElementById('media').style.display = 'block';
        }
        navigator.mediaDevices.getUserMedia(constraints).then(function(stream) {
            stream.getTracks().forEach(function(track) {
                pc.addTrack(track, stream);
            });
            return negotiate();
        }, function(err) {
            alert('Could not acquire media: ' + err);
        });
    } else {
        negotiate();
    }

    document.getElementById('stop').style.display = 'inline-block';
}

function stop() {
    document.getElementById('stop').style.display = 'none';

    // close data channel
    if (dc) {
        dc.close();
    }

    // close transceivers
    if (pc.getTransceivers) {
        pc.getTransceivers().forEach(function(transceiver) {
            transceiver.stop();
        });
    }

    // close local audio / video
    pc.getSenders().forEach(function(sender) {
        sender.track.stop();
    });

    // close peer connection
    setTimeout(function() {
        pc.close();
    }, 500);
}

function sdpFilterCodec(codec, realSpd){
    var allowed = []
    var codecRegex = new RegExp('a=rtpmap:([0-9]+) '+escapeRegExp(codec))
    var videoRegex = new RegExp('(m=video .*?)( ([0-9]+))*\\s*$')
    
    var lines = realSpd.split('\n');

    var isVideo = false;
    for(var i = 0; i < lines.length; i++){
        if (lines[i].startsWith('m=video ')) {
            isVideo = true;
        } else if (lines[i].startsWith('m=')) {
            isVideo = false;
        }

        if (isVideo) {
            var match = lines[i].match(codecRegex)
            if (match) {
                allowed.push(parseInt(match[1]))
            }
        }
    }

    var skipRegex = 'a=(fmtp|rtcp-fb|rtpmap):([0-9]+)'
    var sdp = ""

    var isVideo = false;
    for(var i = 0; i < lines.length; i++){
        if (lines[i].startsWith('m=video ')) {
            isVideo = true;
        } else if (lines[i].startsWith('m=')) {
            isVideo = false;
        }

        if (isVideo) {
            var skipMatch = lines[i].match(skipRegex);
            if (skipMatch && !allowed.includes(parseInt(skipMatch[2]))) {
                continue;
            } else if (lines[i].match(videoRegex)) {
                sdp+=lines[i].replace(videoRegex, '$1 '+allowed.join(' ')) + '\n'
            } else {
                sdp += lines[i] + '\n'
            }
        } else {
            sdp += lines[i] + '\n'
        }
    }

    return sdp;
}

function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // $& means the whole matched string
}
