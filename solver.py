"""
Решатель задач с экрана.
Выделяете область экрана (или берёте картинку/текст из буфера обмена),
справа появляется окно с режимами «Простой» / «Глубокий» и решением.
Нейросеть работает локально через Ollama (бесплатно и без лимитов).
"""
import base64
import io
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

import requests
from PIL import ImageGrab

# ---------- Настройки ----------
# В собранном .exe настройки лежат рядом с exe-файлом, а не во временной папке
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_CONFIG = {
    "ollama_url": "http://localhost:11434",
    # Модель с поддержкой изображений. Для слабых ПК: "gemma3:4b",
    # для мощных: "qwen2.5vl:7b" или "gemma3:12b".
    "model": "gemma3:4b",
    "window_width": 460,
    "hotkey": "<ctrl>+<shift>+s",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


CFG = load_config()

PROMPTS = {
    "simple": (
        "Реши задачу. Дай КРАТКОЕ и чёткое решение: только ключевые шаги "
        "(не больше 4-5 строк) и в конце строку «Ответ: ...». "
        "Без лишних объяснений. Отвечай на языке задачи."
    ),
    "deep": (
        "Реши задачу и дай ПОДРОБНОЕ объяснение: что дано, какой метод "
        "используется и почему, каждый шаг решения с пояснениями, проверка "
        "результата и в конце строку «Ответ: ...». Отвечай на языке задачи."
    ),
}

# Чёткие координаты на Windows с масштабированием экрана
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# ---------- Работа с Ollama ----------
def ollama_ready():
    """Возвращает (ok, сообщение)."""
    try:
        r = requests.get(CFG["ollama_url"] + "/api/tags", timeout=3)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        model = CFG["model"]
        if not any(n == model or n.split(":")[0] == model for n in names):
            return False, (
                f"Модель «{model}» не найдена.\n"
                f"Откройте терминал и выполните:\n\n    ollama pull {model}\n"
            )
        return True, ""
    except Exception:
        return False, (
            "Не удаётся подключиться к Ollama.\n"
            "Установите его с https://ollama.com и запустите, затем перезапустите программу."
        )


def stream_solve(mode, image=None, text=None):
    """Генератор кусочков ответа от модели."""
    msg = {"role": "user", "content": PROMPTS[mode]}
    if image is not None:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        msg["images"] = [base64.b64encode(buf.getvalue()).decode()]
    if text:
        msg["content"] += "\n\nЗадача:\n" + text
    payload = {"model": CFG["model"], "messages": [msg], "stream": True}
    with requests.post(
        CFG["ollama_url"] + "/api/chat", json=payload, stream=True, timeout=600
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            if "error" in data:
                raise RuntimeError(data["error"])
            chunk = data.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if data.get("done"):
                break


# ---------- Выделение области экрана ----------
class RegionSelector:
    def __init__(self, root, on_done):
        self.on_done = on_done
        self.top = tk.Toplevel(root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.3)
        self.top.configure(bg="black", cursor="crosshair")
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.top.bind("<Escape>", lambda e: self.cancel())
        self.top.focus_force()

    def press(self, e):
        self.start = (e.x_root, e.y_root)
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="red", width=2
        )
        self._cstart = (e.x, e.y)

    def drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, self._cstart[0], self._cstart[1], e.x, e.y)

    def release(self, e):
        if not self.start:
            return self.cancel()
        x1, y1 = self.start
        x2, y2 = e.x_root, e.y_root
        box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.top.destroy()
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            return self.on_done(None)
        # небольшая пауза, чтобы затемнение успело исчезнуть
        self.on_done(box)

    def cancel(self):
        self.top.destroy()
        self.on_done(None)


# ---------- Главное окно ----------
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Решатель задач")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = CFG["window_width"]
        self.root.geometry(f"{w}x{sh - 90}+{sw - w}+0")
        self.root.attributes("-topmost", True)
        self.q = queue.Queue()
        self.busy = False
        self.last_input = None  # ("image", img) или ("text", str)

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Режим:").pack(side="left")
        self.mode = tk.StringVar(value="simple")
        ttk.Radiobutton(top, text="Простой", variable=self.mode, value="simple").pack(
            side="left", padx=6
        )
        ttk.Radiobutton(top, text="Глубокий", variable=self.mode, value="deep").pack(
            side="left"
        )

        btns = ttk.Frame(self.root, padding=(8, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="📷 Выделить область", command=self.pick_region).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(btns, text="📋 Из буфера", command=self.from_clipboard).pack(
            side="left", padx=4
        )
        ttk.Button(btns, text="🔄 Заново", command=self.resolve).pack(side="left", padx=4)

        self.status = ttk.Label(self.root, text="Готово", padding=8, foreground="gray")
        self.status.pack(fill="x")

        frame = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        frame.pack(fill="both", expand=True)
        self.out = tk.Text(frame, wrap="word", font=("Segoe UI", 11), padx=6, pady=6)
        sb = ttk.Scrollbar(frame, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out.pack(side="left", fill="both", expand=True)

        self.root.after(100, self.poll)
        self.start_hotkey()

        ok, msg = ollama_ready()
        if not ok:
            self.show(msg)

    # --- вывод ---
    def show(self, text, append=False):
        if not append:
            self.out.delete("1.0", "end")
        self.out.insert("end", text)
        self.out.see("end")

    def set_status(self, text):
        self.status.config(text=text)

    # --- глобальная горячая клавиша (если установлен pynput) ---
    def start_hotkey(self):
        try:
            from pynput import keyboard

            hk = keyboard.GlobalHotKeys(
                {CFG["hotkey"]: lambda: self.q.put(("hotkey", None))}
            )
            hk.daemon = True
            hk.start()
            self.set_status(f"Готово. Горячая клавиша: {CFG['hotkey']}")
        except Exception:
            pass

    # --- получение задачи ---
    def pick_region(self):
        if self.busy:
            return
        self.root.withdraw()
        self.root.after(150, lambda: RegionSelector(self.root, self.region_done))

    def region_done(self, box):
        if box is None:
            self.root.deiconify()
            return
        self.root.after(200, lambda: self._grab(box))

    def _grab(self, box):
        try:
            img = ImageGrab.grab(bbox=box, all_screens=True)
        except TypeError:
            img = ImageGrab.grab(bbox=box)
        except Exception as e:
            self.root.deiconify()
            return self.show(f"Не удалось сделать снимок: {e}")
        self.root.deiconify()
        self.last_input = ("image", img)
        self.resolve()

    def from_clipboard(self):
        if self.busy:
            return
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None
        if clip is not None and hasattr(clip, "size"):
            self.last_input = ("image", clip)
        else:
            try:
                txt = self.root.clipboard_get().strip()
            except tk.TclError:
                txt = ""
            if not txt:
                return self.show("В буфере обмена нет ни картинки, ни текста.")
            self.last_input = ("text", txt)
        self.resolve()

    # --- решение ---
    def resolve(self):
        if self.busy or not self.last_input:
            return
        ok, msg = ollama_ready()
        if not ok:
            return self.show(msg)
        self.busy = True
        self.set_status("Решаю…")
        self.show("")
        kind, data = self.last_input
        mode = self.mode.get()

        def worker():
            try:
                gen = (
                    stream_solve(mode, image=data)
                    if kind == "image"
                    else stream_solve(mode, text=data)
                )
                for chunk in gen:
                    self.q.put(("chunk", chunk))
                self.q.put(("done", None))
            except Exception as e:
                self.q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "chunk":
                    self.show(val, append=True)
                elif kind == "done":
                    self.busy = False
                    self.set_status("Готово")
                elif kind == "error":
                    self.busy = False
                    self.set_status("Ошибка")
                    self.show(f"\nОшибка: {val}", append=True)
                elif kind == "hotkey":
                    self.root.deiconify()
                    self.pick_region()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
