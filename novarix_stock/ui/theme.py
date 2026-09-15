APP_STYLESHEET = """
QWidget {
    color: #edf4ff;
    font-family: "Segoe UI";
    font-size: 13px;
}
QMainWindow, QWidget#appRoot {
    background-color: #070a12;
}
QLabel { background: transparent; }

QFrame#headerCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #111b2e, stop:0.58 #0d1525, stop:1 #17122d);
    border: 1px solid #29405e;
    border-radius: 18px;
}
QLabel#brandLogo {
    background-color: #ffffff;
    border: 1px solid #59dcff;
    border-radius: 12px;
    padding: 3px;
}
QLabel#brand {
    color: #ffffff;
    font-size: 25px;
    font-weight: 900;
    letter-spacing: 2px;
}
QLabel#brandSuffix {
    color: #55ddff;
    font-size: 13px;
    font-weight: 700;
}
QLabel#subtitle {
    color: #8293ae;
    font-size: 12px;
    letter-spacing: 1px;
}
QLabel#counter {
    color: #bcefff;
    background-color: rgba(23, 47, 73, 170);
    border: 1px solid #315373;
    border-radius: 13px;
    padding: 7px 13px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#planBadge {
    color: #9feeff;
    background-color: #10243a;
    border: 1px solid #2f607e;
    border-radius: 11px;
    padding: 5px 10px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}
QPushButton#proButton {
    min-height: 16px;
    color: #d8c9ff;
    background-color: #241b43;
    border-color: #634ba5;
    padding: 6px 10px;
    font-size: 10px;
}
QPushButton#proButton:hover { color: #ffffff; border-color: #9b7cff; }

QFrame#metricCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #111a2a, stop:1 #0c1321);
    border: 1px solid #223651;
    border-radius: 13px;
}
QFrame#metricCard[accent="metricCyan"] { border-top: 2px solid #45e6ff; }
QFrame#metricCard[accent="metricBlue"] { border-top: 2px solid #4b8dff; }
QFrame#metricCard[accent="metricViolet"] { border-top: 2px solid #956dff; }
QFrame#metricDot { border-radius: 3px; }
QFrame#metricDot[accent="metricCyan"] { background-color: #45e6ff; }
QFrame#metricDot[accent="metricBlue"] { background-color: #4b8dff; }
QFrame#metricDot[accent="metricViolet"] { background-color: #956dff; }
QLabel#metricTitle {
    color: #8493ab;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#metricValue {
    color: #f7faff;
    font-size: 23px;
    font-weight: 800;
}

QFrame#actionBar {
    background-color: #0d1422;
    border: 1px solid #1e3049;
    border-radius: 12px;
}
QPushButton {
    min-height: 20px;
    background-color: #152238;
    color: #dce8f8;
    border: 1px solid #2b405e;
    border-radius: 8px;
    padding: 8px 13px;
    font-size: 12px;
    font-weight: 650;
}
QPushButton:hover {
    color: #ffffff;
    background-color: #1b2d49;
    border-color: #54dfff;
}
QPushButton:pressed { background-color: #101d31; padding-top: 9px; padding-bottom: 7px; }
QPushButton#primaryButton {
    color: #05131d;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #55eaff, stop:1 #49bfff);
    border-color: #7af1ff;
    font-weight: 800;
}
QPushButton#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #8cf4ff, stop:1 #69ceff);
}
QPushButton#saleButton {
    color: #f0eaff;
    background-color: #3d2c71;
    border-color: #7658c9;
}
QPushButton#saleButton:hover { background-color: #4d388d; border-color: #a182ff; }
QPushButton#excelButton { color: #b9d9ff; background-color: #132238; }
QPushButton#excelButton:hover { color: #ddf6ff; border-color: #4c9fff; }
QPushButton#dangerButton { color: #ffabbc; background-color: #1b1722; border-color: #613040; }
QPushButton#dangerButton:hover { color: #ffd5dd; background-color: #421d29; border-color: #f05878; }
QPushButton:disabled {
    color: #536176;
    background-color: #101724;
    border-color: #1b2638;
}

QFrame#tableCard {
    background-color: #0d1422;
    border: 1px solid #263b58;
    border-radius: 15px;
}
QWidget#tableHeading {
    background-color: #0f1828;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    border-bottom: 1px solid #20344f;
}
QLabel#tableTitle { color: #f5f8ff; font-size: 16px; font-weight: 800; }
QLabel#tableHint { color: #64748e; font-size: 11px; }
QTableWidget {
    background-color: #0c1320;
    alternate-background-color: #0f1726;
    border: 0;
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 14px;
    gridline-color: transparent;
    selection-background-color: #153c55;
    selection-color: #ffffff;
    outline: 0;
}
QTableWidget::item {
    padding: 8px 9px;
    border-bottom: 1px solid #1a2a40;
}
QTableWidget::item:hover { background-color: #132238; }
QTableWidget::item:selected { background-color: #173c55; color: #ffffff; }
QHeaderView::section {
    background-color: #121d2f;
    color: #8092ae;
    border: 0;
    border-right: 1px solid #1c3049;
    border-bottom: 1px solid #2a4262;
    padding: 9px 6px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
}
QWidget#photoCell, QWidget#statusContainer { background: transparent; }
QLabel#productPhoto {
    background-color: #111b2b;
    border: 1px solid #315170;
    border-radius: 10px;
}
QLabel#photoPlaceholder {
    color: #69e7ff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #16283e, stop:1 #241b43);
    border: 1px solid #3b5c7e;
    border-radius: 10px;
    font-size: 16px;
    font-weight: 800;
}
QFrame#statusBadge { border-radius: 10px; }
QFrame#statusBadge[state="ok"] { background-color: #102b29; border: 1px solid #235d52; }
QFrame#statusBadge[state="restock"] { background-color: #321924; border: 1px solid #783148; }
QFrame#statusBadge[state="locked"] { background-color: #211b35; border: 1px solid #5c4791; }
QFrame#statusDot { border-radius: 3px; }
QFrame#statusDot[state="ok"] { background-color: #54e1b0; }
QFrame#statusDot[state="restock"] { background-color: #ff6482; }
QFrame#statusDot[state="locked"] { background-color: #a987ff; }
QLabel#statusText { font-size: 9px; font-weight: 800; letter-spacing: 1px; }
QLabel#statusText[state="ok"] { color: #6be9bd; }
QLabel#statusText[state="restock"] { color: #ff91a7; }
QLabel#statusText[state="locked"] { color: #c5b1ff; }

QLineEdit, QDoubleSpinBox, QSpinBox {
    background-color: #0b1321;
    color: #ffffff;
    border: 1px solid #2b405e;
    border-radius: 8px;
    padding: 8px;
    selection-background-color: #6554d9;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #4ee5ff; }
QDialog { background-color: #090f1a; }
QDialog QLabel { background: transparent; }
QLabel#dialogTitle { font-size: 20px; font-weight: 800; color: #ffffff; }
QLabel#fieldLabel { color: #8f9eb6; font-size: 12px; }
QLabel#photoPreview {
    background-color: #090e19;
    color: #68758a;
    border: 1px dashed #3b5575;
    border-radius: 10px;
}
QMessageBox { background-color: #101827; }
QToolTip { background-color: #17243a; color: white; border: 1px solid #4ee5ff; padding: 5px; }
QScrollBar:vertical { background: #0b121e; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2a405d; min-height: 30px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #3d5f87; }
QScrollBar:horizontal { background: #0b121e; height: 9px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2a405d; min-width: 30px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
"""
