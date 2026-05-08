from pywinauto import Desktop
from deep_translator import GoogleTranslator
import time

caption_window = Desktop(
    backend="uia"
).window(title_re=".*实时辅助字幕.*")

last_text = ""

print("开始监听并翻译字幕...\n")

while True:
    try:
        for child in caption_window.descendants():

            if child.friendly_class_name() == "Static":

                text = child.window_text().strip()

                if text and text != last_text:

                    last_text = text

                    translated = GoogleTranslator(
                        source='auto',
                        target='zh-CN'
                    ).translate(text)

                    print("\n========== 原文 ==========")
                    print(text)

                    print("========== 翻译 ==========")
                    print(translated)

        time.sleep(1)

    except Exception as e:
        print("错误:", e)