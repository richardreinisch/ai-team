
const MiniWebSocket = (function() {

    console.log("Loaded MiniWebSocket.");

    let socket;
    let endpoint;
    const callbacks = { };

    function setUrl(url) {
        endpoint = url;
    }

    function connect() {
        if (endpoint != undefined) {
            console.log("Connect WebSocket.");
            socket = new WebSocket(endpoint);
            socket.onopen = function(event) { onOpen(event) };
            socket.onmessage = function(event) { onMessage(event) };
            socket.onclose = function(event) { onClose(event) };
            socket.onerror = function(event) { onError(event) };
        } else {
            console.log("Endpoint not given.");
        }
    }

    function onMessage(event) {
        data = JSON.parse(event.data);
        callbacks["message"](data);
    }

    function sendMessage(data) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(data);
        } else {
            console.log("WebSocket not ready yet.");
        }
    }

    function onOpen(event) {
        console.log("WebSocket connection established.");
    }

    function onClose(event) {
        console.log('WebSocket is closed. Reconnect will be attempted in 5 second.', event.reason);
        setTimeout(function() {
            connect(endpoint);
        }, 5000);
    };

    function onError(event) {
        console.log("Websocket connection error.");
        socket.close();
    }

    return {
        url: function(url) { setUrl(url); },
        connect: connect,
        send: function(data) { sendMessage(data) },
        on: function(eventType, callback) { callbacks[eventType] = callback; }
    }

})();

const ElementRenderer = (function() {

    console.log("Loaded ElementRenderer.");

    function renderRequestContainer(requestText) {
        const innerContainer = $("<div class='row g-0'>");
        innerContainer.append($("<div class='col-md-11'>").append($("<div class='card-body'>")
        .append($("<p class='card-text'>").text(requestText))));
        $("#chatHistoryContainer").append($("<div class='row'>").append($("<div class='col-12'>")
        .append($("<div class='card mb-3 float-right' style='max-width: 700px;'>")
        .append(innerContainer))));
    }

    function renderResponseContainer(response) {

        const innerContainer = $("<div class='row g-0'>");
        const modelName = response["model"];
        const image = $("<img src='images/" + modelName + ".png' class='img-fluid rounded-start' alt='" +
            response["character"]["name"] + "'>")
        innerContainer.append($("<div class='col-md-1'>").append(image));
        const textToShow = response["message"]["content"];
        const isDevModel = modelName === "james_ryan_wick";
        const codeBlock = $("<code>").text(textToShow);
        const backgroundColor = response["character"]["color-web"];

        if (isDevModel) {
            innerContainer.append($("<div class='col-md-11'>").append($("<div class='card-body'>")
                .append($("<p class='card-text response-container-code'>").append($("<pre>").append(codeBlock)))));
        } else {
            innerContainer.append($("<div class='col-md-11'>").append($("<div class='card-body'>")
                .append($("<p class='card-text response-container-text'>").text(textToShow))));
        }

        $("#chatHistoryContainer").append($("<div class='row'>").append($("<div class='col-12'>")
            .append($("<div class='card mb-3' style='max-width: 1300px; background-color: " + backgroundColor + ";'>")
            .append(innerContainer))));

        hljs.highlightElement(codeBlock[0]);

    }

    return {
        renderRequestContainer: function(data) { renderRequestContainer(data); },
        renderResponseContainer: function(data) { renderResponseContainer(data); }
    }

})();

const ProgressBar = (function() {

    console.log("Loaded ProgressBar.");

    let intervalProgress = undefined;
    let progressValue = 0;
    let timespanLastRequest = 30000;

    function initProgress() {
        clearProgress();
        intervalProgress = setInterval(updateProgressBar, 1000);
    }

    function updateProgressBar() {
        progressValue += 100 / (timespanLastRequest / 1000);
        $('.progress-bar').css('width', progressValue + '%').attr('aria-valuenow', progressValue);
        if (progressValue >= 100) {
            clearInterval(intervalProgress);
        }
    }

    function clearProgress() {
        clearInterval(intervalProgress);
        progressValue = 0;
        $('.progress-bar').css('width', progressValue + '%').attr('aria-valuenow', progressValue);
    }

    function setTimespanLastRequest(timespan) {
        timespanLastRequest = timespan;
    }

    return {
        initProgress: initProgress,
        updateProgressBar: updateProgressBar,
        clearProgress: clearProgress,
        setTimespanLastRequest: function(timespan) { setTimespanLastRequest(timespan); }
    };

})();

const Utils = (function() {
    function getCurrentUnixTimestamp() {
        return Math.floor((new Date()).getTime());
    }
    return { getCurrentUnixTimestamp: getCurrentUnixTimestamp }
})();

const Chat = (function() {

    console.log("Loaded Chat.");

    let selectedCharacter = 0;
    let selectedRecipient = undefined;
    let lastMessageReceived = undefined;
    let timestampLastRequestSent = undefined;

    function start(url) {
        MiniWebSocket.url(url);
        MiniWebSocket.connect();
    }

    MiniWebSocket.on('message', function(data) {

        console.log("WebSocket received Message: " + data["message"]["content"]);

        ProgressBar.setTimespanLastRequest(Utils.getCurrentUnixTimestamp() - timestampLastRequestSent);
        lastMessageReceived = data["message"]["content"];
        ElementRenderer.renderResponseContainer(data);
        ProgressBar.clearProgress();
        scrollBottom();

        if (selectedRecipient != undefined) {
            $("#userInputContainer").hide();
            $("#autoContainer").show();
            const messageFromRecipient = data["character"]["id"] == selectedRecipient;
            if (messageFromRecipient) prepareAndSendMessage(lastMessageReceived, selectedCharacter, true);
            else prepareAndSendMessage(lastMessageReceived, selectedRecipient, true);
        }

    });

    $("#sendMessageButton").on("click", function() {
        const text = $("#text-input").val();
        if (text !== "") {
            prepareAndSendMessage(text,
                selectedRecipient != undefined ? selectedRecipient : selectedCharacter,
                selectedRecipient != undefined);
        }
    });

    $("#stopButton").on("click", function() {
        selectedRecipient = undefined;
        $("#autoContainer").hide();
        $("#userInputContainer").show();
    });

    $(".character-selection").on("click", function(event) {
        if (event.ctrlKey) {
            $(".character-selection img").toggleClass("selectedRecipient", false);
            $("img", this).toggleClass("selectedRecipient", true);
            selectedRecipient = parseInt($(this).attr("data-id"));
        } else {
            $(".character-selection img").toggleClass("selected", false);
            $(".character-selection img").toggleClass("selectedRecipient", false);
            $("img", this).toggleClass("selected", true);
            selectedCharacter = parseInt($(this).attr("data-id"));
            selectedRecipient = undefined;
        }
    });

    function prepareAndSendMessage(text, characterId, isAutoMode) {
        toSend = { "prompt": text, "characterId": characterId, "isAutoMode": isAutoMode };
        console.log("Send: " + JSON.stringify(toSend));
        MiniWebSocket.send(JSON.stringify(toSend));
        timestampLastRequestSent = Utils.getCurrentUnixTimestamp();
        if (selectedRecipient == undefined) ElementRenderer.renderRequestContainer(text);
        $("#text-input").val("");
        ProgressBar.initProgress();
    }

    function scrollBottom() {
        $("html, body").animate({ scrollTop: $(document).height() }, 1000);
    }

    return { start: function(url) { start(url); } }

})();

$(document).ready(function() {
    $('[data-toggle="tooltip"]').tooltip();
    Chat.start("ws://localhost:3000");
});

