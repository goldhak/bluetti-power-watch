import QtQuick
import org.kde.plasma.configuration
import "../code/logic.js" as Logic

ConfigModel {
    ConfigCategory {
        name: Logic.tr("cfg_page", Logic.language(Qt.locale().name))
        icon: "configure"
        source: "ConfigGeneral.qml"
    }
}
