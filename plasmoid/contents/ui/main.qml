// "BLUETTI power watch" widget: shows the watchdog's state.json. The only thing it
// writes is the thresholds, through `bluetti-power-watch set-thresholds`.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.kirigami as Kirigami
import "../code/logic.js" as Logic

PlasmoidItem {
    id: root

    readonly property string lang: Logic.language(Qt.locale().name)

    readonly property string command: Logic.READ_COMMAND

    property var info: Logic.parse("")
    readonly property var st: Logic.status(info, lang)
    readonly property var last: (info.state && st.kind !== "watch" && st.kind !== "setup") ? info.state.last : null
    readonly property bool isCharging: Logic.charging(last)
    readonly property var percent: last ? last.percent : null

    // Thresholds the watchdog really uses, and the ones set in this widget.
    readonly property var thr: Logic.thresholds(info)
    readonly property int cfgLow: Plasmoid.configuration.lowPercent
    readonly property int cfgCritical: Plasmoid.configuration.criticalPercent
    readonly property int cfgStuck: Plasmoid.configuration.stuckMinutes
    readonly property string cfgKey: cfgLow + "/" + cfgCritical + "/" + cfgStuck
    property string writeError: ""

    onCfgKeyChanged: writeTimer.restart()

    Timer {
        id: writeTimer
        interval: 700
        onTriggered: {
            const problem = Logic.thresholdsError(root.cfgLow, root.cfgCritical, root.cfgStuck, root.lang)
            if (problem !== "") {
                root.writeError = problem
                return
            }
            const cmd = Logic.setCommand(root.cfgLow, root.cfgCritical, root.cfgStuck, Date.now())
            if (cmd === "") {
                root.writeError = Logic.tr("bad_values", root.lang)
                return
            }
            writer.connectSource(cmd)
        }
    }

    Plasma5Support.DataSource {
        id: writer
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            root.writeError = data["exit code"] === 0 ? "" : (data["stderr"] || Logic.tr("write_failed", root.lang)).trim()
            disconnectSource(sourceName)
        }
    }

    readonly property color stateColor: {
        if (st.kind === "stuck") return Kirigami.Theme.negativeTextColor
        if (st.kind === "outage") return Logic.isLow(percent, thr)
            ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.neutralTextColor
        if (st.kind === "grid") return Kirigami.Theme.positiveTextColor
        return Kirigami.Theme.disabledTextColor
    }

    Plasmoid.icon: Logic.iconName(percent, isCharging)
    Plasmoid.status: (st.kind === "grid") ? PlasmaCore.Types.ActiveStatus : PlasmaCore.Types.NeedsAttentionStatus
    toolTipMainText: Logic.tr("tooltip", lang) + ": " + st.title
    toolTipSubText: (percent !== null ? Logic.tr("charge_short", lang) + " " + percent + " %. " : "") + st.hint

    Plasma5Support.DataSource {
        id: source
        engine: "executable"
        connectedSources: [root.command]
        interval: 15000
        onNewData: (sourceName, data) => {
            root.info = Logic.parse(data["stdout"])
        }
    }

    compactRepresentation: MouseArea {
        Layout.minimumWidth: row.implicitWidth
        Layout.preferredWidth: row.implicitWidth
        onClicked: root.expanded = !root.expanded

        RowLayout {
            id: row
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                source: Plasmoid.icon
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
            }
            PlasmaComponents.Label {
                text: root.percent !== null ? root.percent + " %" : "?"
                color: root.st.kind === "grid" ? Kirigami.Theme.textColor : root.stateColor
                font.bold: root.st.kind === "stuck" || root.st.kind === "outage"
            }
        }
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        Layout.preferredWidth: Kirigami.Units.gridUnit * 18
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            spacing: Kirigami.Units.smallSpacing
            Kirigami.Icon {
                source: Plasmoid.icon
                Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                Layout.preferredHeight: Kirigami.Units.iconSizes.medium
            }
            Kirigami.Heading {
                level: 3
                text: root.st.title
                color: root.stateColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        PlasmaComponents.Label {
            visible: root.st.hint !== ""
            text: root.st.hint
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        GridLayout {
            visible: root.last !== null
            columns: 2
            columnSpacing: Kirigami.Units.largeSpacing
            rowSpacing: 0
            Layout.topMargin: Kirigami.Units.smallSpacing

            PlasmaComponents.Label { text: Logic.tr("charge", root.lang); opacity: 0.7 }
            PlasmaComponents.Label { text: root.last ? root.last.percent + " %" : ""; font.bold: true }

            PlasmaComponents.Label { text: Logic.tr("voltage", root.lang); opacity: 0.7 }
            PlasmaComponents.Label { text: root.last ? Math.round(root.last.voltage_in) + " " + Logic.tr("volts", root.lang) : "" }

            PlasmaComponents.Label { text: Logic.tr("input", root.lang); opacity: 0.7 }
            PlasmaComponents.Label { text: root.last ? root.last.watts_in + " " + Logic.tr("watts", root.lang) : "" }

            PlasmaComponents.Label { text: Logic.tr("output", root.lang); opacity: 0.7 }
            PlasmaComponents.Label { text: root.last ? root.last.watts_out + " " + Logic.tr("watts", root.lang) : "" }
        }

        PlasmaComponents.Label {
            visible: root.thr !== null && root.st.kind !== "setup"
            text: root.thr ? Logic.tr("thresholds_label", root.lang) + ": " + Logic.thresholdsText(root.thr, root.lang) + "." : ""
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            Layout.topMargin: Kirigami.Units.smallSpacing
        }
        PlasmaComponents.Label {
            visible: root.writeError === "" && root.thr !== null
                && !Logic.sameThresholds(root.thr, root.cfgLow, root.cfgCritical, root.cfgStuck)
            text: Logic.tr("pending", root.lang)
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            color: Kirigami.Theme.neutralTextColor
            font: Kirigami.Theme.smallFont
        }
        PlasmaComponents.Label {
            visible: root.writeError !== ""
            text: Logic.tr("not_saved", root.lang) + ": " + root.writeError
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            color: Kirigami.Theme.negativeTextColor
            font: Kirigami.Theme.smallFont
        }
        PlasmaComponents.Label {
            text: Logic.armedText(root.info, root.lang)
            opacity: 0.7
            font: Kirigami.Theme.smallFont
        }
        PlasmaComponents.Label {
            visible: root.info.mtime > 0
            text: Logic.tr("updated", root.lang, { s: Math.max(0, root.info.now - root.info.mtime) })
            opacity: 0.7
            font: Kirigami.Theme.smallFont
        }
        // On the desktop the widget can be taller than its content: keep rows on top.
        Item { Layout.fillHeight: true }
    }
}
