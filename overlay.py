import sys
from PyQt6.QtWidgets import QApplication, QLabel, QWidget
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

window = QWidget()

window.setWindowFlags(
    Qt.WindowType.FramelessWindowHint |
    Qt.WindowType.WindowStaysOnTopHint
)

window.setAttribute(
    Qt.WidgetAttribute.WA_TranslucentBackground
)

label = QLabel("实时翻译字幕", window)

label.setStyleSheet("""
    color: white;
    font-size: 30px;
    background-color: rgba(0,0,0,180);
    padding: 10px;
    border-radius: 10px;
""")

window.resize(800, 100)

window.show()

sys.exit(app.exec())