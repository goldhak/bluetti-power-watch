// Watchdog thresholds. Each field has fixed limits, the same as THRESHOLD_RANGES
// in bluetti_power_watch.py. The fields must NOT limit each other: mutual limits
// clipped values while the page was opening (a saved 8 % became 7 %). An
// impossible combination is shown in red and the widget does not write it.
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import "../code/logic.js" as Logic

KCM.SimpleKCM {
    readonly property string lang: Logic.language(Qt.locale().name)
    property alias cfg_lowPercent: lowSpin.value
    property alias cfg_criticalPercent: criticalSpin.value
    property alias cfg_stuckMinutes: stuckSpin.value

    readonly property string problem: Logic.thresholdsError(lowSpin.value, criticalSpin.value, stuckSpin.value, lang)

    Kirigami.FormLayout {
        QQC2.SpinBox {
            id: lowSpin
            Kirigami.FormData.label: Logic.tr("cfg_low", lang)
            from: 10
            to: 90
        }

        QQC2.SpinBox {
            id: criticalSpin
            Kirigami.FormData.label: Logic.tr("cfg_critical", lang)
            from: 5
            to: 50
        }

        QQC2.SpinBox {
            id: stuckSpin
            Kirigami.FormData.label: Logic.tr("cfg_stuck", lang)
            from: 1
            to: 60
        }

        QQC2.Label {
            visible: problem !== ""
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            color: Kirigami.Theme.negativeTextColor
            text: problem + " " + Logic.tr("err_not_saved", lang)
        }

        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            wrapMode: Text.WordWrap
            opacity: 0.7
            text: Logic.tr("cfg_note", lang)
        }
    }
}
