"""Shared warm neutral desktop theme; artwork is drawn locally at runtime."""
from __future__ import annotations

from functools import lru_cache
import sys
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFilter, ImageTk


class Theme:
    bg = "#f5f4f0"
    sidebar = "#eeede7"
    header = "#fafaf7"
    card = "#ffffff"
    card_alt = "#f0efe9"
    input_bg = "#f8f8f5"
    border = "#e4e3db"
    border_hover = "#d2d1c7"
    text = "#272c27"
    text_dim = "#555e55"
    muted = "#7a8077"
    accent = "#49654a"
    accent_hover = "#375039"
    accent_soft = "#e4eddf"
    record = "#ff3b30"
    record_hover = "#e52f26"
    success = "#3e744c"
    success_soft = "#e8f2e6"
    warning = "#a86b00"
    warning_soft = "#fff2d5"
    error_soft = "#ffebe9"
    overlay = "#111114"


class Fonts:
    family = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI"
    latin = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
    ui = (family, 10)
    ui_bold = (family, 10, "bold")
    title = (family, 22, "bold")
    subtitle = (family, 10)
    section = (family, 11, "bold")
    timer = (latin if sys.platform == "darwin" else "Segoe UI Light", 46)
    mono = (latin, 10)
    small = (family, 9)


def studio_icon() -> Image.Image:
    size = 256
    image = Image.new("RGBA", (size, size))
    gradient = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(gradient)
    for y in range(size):
        value = y / (size - 1)
        draw.line((0, y, size, y), fill=(round(100-29*value), round(128-27*value), round(83-14*value), 255))
    mask = Image.new("L", (size, size))
    ImageDraw.Draw(mask).rounded_rectangle((4, 4, 251, 251), radius=56, fill=255)
    image.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((52, 61, 204, 194), radius=59, fill="#ecd4a5")
    draw.ellipse((80, 87, 91, 98), fill="#c49b66")
    draw.ellipse((167, 143, 176, 152), fill="#c49b66")
    draw.polygon(((112, 104), (112, 157), (158, 130)), fill="#49654a")
    return image


@lru_cache(maxsize=32)
def rounded_image(width: int, height: int, fill: str, background: str,
                  radius: int = 12, border: str | None = None, shadow: bool = False) -> Image.Image:
    scale = 2
    w, h = max(2, width) * scale, max(2, height) * scale
    image = Image.new("RGBA", (w, h), background)
    inset = 2 if shadow else 0
    bottom = h - (5 * scale if shadow else 1)
    if shadow:
        layer = Image.new("RGBA", (w, h))
        ImageDraw.Draw(layer).rounded_rectangle(
            (inset * scale, 3 * scale, w - inset * scale - 1, h - 2 * scale),
            radius=radius * scale, fill=(29, 29, 31, 16))
        image = Image.alpha_composite(image, layer.filter(ImageFilter.GaussianBlur(3 * scale)))
    ImageDraw.Draw(image).rounded_rectangle(
        (inset * scale, scale if shadow else 0, w - inset * scale - 1, bottom),
        radius=radius * scale, fill=fill, outline=border, width=scale)
    return image.convert("RGB").resize((max(2, width), max(2, height)), Image.Resampling.LANCZOS)


class Card(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        self._parent_bg = master.cget("bg")
        super().__init__(master, bg=self._parent_bg, padx=24, pady=20, **kwargs)
        self._surface = tk.Label(self, bg=self._parent_bg, bd=0, padx=0, pady=0)
        self._surface.place(x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
        self._surface.lower()
        self.bind("<Configure>", self._paint, add="+")

    def _paint(self, event) -> None:
        if event.width < 2 or event.height < 2:
            return
        image = rounded_image(event.width, event.height, Theme.card, self._parent_bg,
                              radius=14, border=Theme.border, shadow=True)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self._surface.configure(image=self._photo)
        self._surface.lower()


class PillButton(tk.Frame):
    def __init__(self, master, text, command, variant="primary", width=12, **kwargs) -> None:
        super().__init__(master, bg=master.cget("bg"), **kwargs)
        self.command, self.variant, self.text = command, variant, text
        self._enabled, self._hover = True, False
        self._font = tkfont.Font(root=self, font=Fonts.ui_bold)
        self._width = max(64, round(self._font.measure("0") * width + 24), self._font.measure(text) + 28)
        self.label = tk.Canvas(self, width=self._width, height=36, highlightthickness=0,
                               bg=self.cget("bg"), cursor="hand2", takefocus=True)
        self.label.pack()
        self.label.bind("<Button-1>", self._click)
        self.label.bind("<Return>", self._click)
        self.label.bind("<space>", self._click)
        self.label.bind("<Enter>", lambda _e: self._apply(True))
        self.label.bind("<Leave>", lambda _e: self._apply(False))
        self.label.bind("<FocusIn>", lambda _e: self._apply(self._hover))
        self.label.bind("<FocusOut>", lambda _e: self._apply(self._hover))
        self._apply()

    def configure_text(self, text: str) -> None:
        self.text = text
        self._apply(self._hover)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.label.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self._apply()

    def _click(self, _event=None) -> None:
        if self._enabled:
            self.command()
        return "break"

    def _apply(self, hover: bool = False) -> None:
        self._hover = hover
        palettes = {
            "primary": (Theme.accent, Theme.accent_hover, "white"),
            "danger": (Theme.record, Theme.record_hover, "white"),
            "ghost": (Theme.card_alt, Theme.border, Theme.text_dim),
            "success": (Theme.success_soft, "#d7eddd", Theme.success),
            "muted": (Theme.card_alt, Theme.border_hover, Theme.muted),
        }
        idle, hovered, foreground = palettes.get(self.variant, palettes["primary"])
        fill = (hovered if hover else idle) if self._enabled else Theme.input_bg
        foreground = foreground if self._enabled else "#a7aaa1"
        focused = self._enabled and self.label.focus_get() == self.label
        image = rounded_image(self._width, 36, fill, self.cget("bg"), radius=10,
                              border=Theme.accent if focused else None)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.label.delete("all")
        self.label.create_image(0, 0, image=self._photo, anchor="nw")
        self.label.create_text(self._width / 2, 18, text=self.text, fill=foreground, font=Fonts.ui_bold)


class SegmentedControl(tk.Frame):
    def __init__(self, master, options, variable, command=None) -> None:
        super().__init__(master, bg=master.cget("bg"), padx=4, pady=4)
        self.variable, self.command, self.enabled = variable, command, True
        self._font = tkfont.Font(root=self, font=Fonts.ui)
        self.buttons = {}
        self._photos = {}
        self._surface = tk.Label(self, bd=0, bg=self.cget("bg"))
        self._surface.place(x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
        self._surface.lower()
        for value, text in options:
            width = self._font.measure(text) + 24
            button = tk.Canvas(self, width=width, height=30, highlightthickness=0,
                               bg=Theme.card_alt, cursor="hand2", takefocus=True)
            button.pack(side="left")
            button._caption = text
            button._width = width
            button.bind("<Button-1>", lambda _e, item=value: self._select(item))
            button.bind("<Return>", lambda _e, item=value: self._select(item))
            button.bind("<space>", lambda _e, item=value: self._select(item))
            button.bind("<FocusIn>", lambda _e: self.refresh())
            button.bind("<FocusOut>", lambda _e: self.refresh())
            button.bind("<Right>", lambda _e, item=value: self._step(item, 1))
            button.bind("<Left>", lambda _e, item=value: self._step(item, -1))
            self.buttons[value] = button
        self._trace = self.variable.trace_add("write", lambda *_: self.refresh())
        self.bind("<Configure>", self._paint, add="+")
        self.refresh()

    def _paint(self, event) -> None:
        self._background_photo = ImageTk.PhotoImage(
            rounded_image(event.width, event.height, Theme.card_alt, self.cget("bg"), radius=10), master=self)
        self._surface.configure(image=self._background_photo)
        self._surface.lower()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.refresh()

    def _select(self, value: str) -> str:
        if self.enabled and self.variable.get() != value:
            self.variable.set(value)
            if self.command:
                self.command()
        return "break"

    def _step(self, value: str, direction: int) -> str:
        values = list(self.buttons)
        target = values[(values.index(value) + direction) % len(values)]
        if self.enabled:
            self.buttons[target].focus_set()
            self._select(target)
        return "break"

    def refresh(self) -> None:
        selected = self.variable.get()
        for value, button in self.buttons.items():
            active = value == selected
            fill = Theme.card if active else Theme.card_alt
            foreground = Theme.accent if active else Theme.text_dim
            if not self.enabled:
                foreground = "#a0a0a8"
            button.configure(cursor="hand2" if self.enabled else "arrow", takefocus=self.enabled)
            focus = self.enabled and self.focus_get() == button
            image = rounded_image(button._width, 30, fill, Theme.card_alt, radius=7,
                                  border=Theme.accent if focus else ("#dedee4" if active else None))
            self._photos[value] = ImageTk.PhotoImage(image, master=self)
            button.delete("all")
            button.create_image(0, 0, image=self._photos[value], anchor="nw")
            button.create_text(button._width / 2, 15, text=button._caption, fill=foreground, font=Fonts.ui)

    def destroy(self) -> None:
        try:
            self.variable.trace_remove("write", self._trace)
        except tk.TclError:
            pass
        super().destroy()


class NavButton(tk.Frame):
    def __init__(self, master, text, command, icon="record") -> None:
        super().__init__(master, bg=Theme.sidebar)
        self.command, self.text, self.icon = command, text, icon
        self.active = False
        self.label = tk.Canvas(self, height=42, width=176, bg=Theme.sidebar,
                               highlightthickness=0, cursor="hand2", takefocus=True)
        self.label.pack(fill="x", expand=True)
        self.label.bind("<Configure>", lambda _e: self.set_active(self.active))
        self.label.bind("<Button-1>", lambda _e: self.command())
        self.label.bind("<Return>", lambda _e: self.command())
        self.label.bind("<space>", lambda _e: self.command())
        self.label.bind("<FocusIn>", lambda _e: self.set_active(self.active))
        self.label.bind("<FocusOut>", lambda _e: self.set_active(self.active))
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.active = active
        width = max(176, self.label.winfo_width())
        foreground = Theme.accent if active else Theme.text_dim
        fill = Theme.accent_soft if active else Theme.sidebar
        focus = self.label.focus_get() == self.label
        self._photo = ImageTk.PhotoImage(
            rounded_image(width, 42, fill, Theme.sidebar, radius=10,
                          border=Theme.accent if focus else None), master=self)
        self.label.delete("all")
        self.label.create_image(0, 0, image=self._photo, anchor="nw")
        if active:
            self.label.create_line(5, 14, 5, 28, fill=Theme.accent, width=3)
        if self.icon == "record":
            self.label.create_oval(16, 12, 34, 30, outline=foreground, width=1.5)
            self.label.create_oval(21, 17, 29, 25, fill=foreground, outline="")
        elif self.icon == "convert":
            self.label.create_line(16, 16, 34, 16, fill=foreground, width=2, arrow="last")
            self.label.create_line(34, 26, 16, 26, fill=foreground, width=2, arrow="last")
        elif self.icon == "compress":
            self.label.create_rectangle(15, 11, 35, 31, outline=foreground, width=1.5)
            self.label.create_line(17, 13, 23, 19, fill=foreground, width=1.5, arrow="last")
            self.label.create_line(33, 29, 27, 23, fill=foreground, width=1.5, arrow="last")
        elif self.icon == "audio":
            for offset, height in ((0, 8), (5, 17), (10, 24), (15, 13)):
                x = 17 + offset
                self.label.create_line(x, 21 - height / 2, x, 21 + height / 2,
                                       fill=foreground, width=2)
        else:
            self.label.create_oval(16, 23, 23, 30, outline=foreground, width=1.5)
            self.label.create_oval(27, 23, 34, 30, outline=foreground, width=1.5)
            self.label.create_line(20, 25, 32, 12, fill=foreground, width=1.5)
            self.label.create_line(30, 25, 18, 12, fill=foreground, width=1.5)
        self.label.create_text(48, 21, text=self.text, anchor="w", fill=foreground,
                               font=Fonts.ui_bold if active else Fonts.ui)


class Switch(tk.Canvas):
    def __init__(self, master, variable: tk.BooleanVar) -> None:
        super().__init__(master, width=42, height=26, bg=master.cget("bg"),
                         highlightthickness=0, cursor="hand2", takefocus=True)
        self.variable, self.enabled = variable, True
        self._trace = variable.trace_add("write", lambda *_: self._paint())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())
        self._paint()

    def _toggle(self, _event=None) -> str:
        if self.enabled:
            self.variable.set(not self.variable.get())
        return "break"

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self.enabled = kwargs["state"] != "disabled"
            kwargs["cursor"] = "hand2" if self.enabled else "arrow"
            kwargs["takefocus"] = self.enabled
        result = super().configure(cnf, **kwargs)
        if hasattr(self, "variable"):
            self._paint()
        return result

    config = configure

    def _paint(self) -> None:
        active = self.variable.get()
        fill = "#34c759" if active else "#d5d5dc"
        if not self.enabled:
            fill = "#bfdcc7" if active else "#e5e5e9"
        image = rounded_image(42, 26, fill, self.cget("bg"), radius=13,
                              border=Theme.accent if self.enabled and self.focus_get() == self else None)
        draw = ImageDraw.Draw(image)
        left = 19 if active else 3
        draw.ellipse((left, 3, left + 20, 23), fill="white", outline="#ededf0")
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")

    def destroy(self) -> None:
        try:
            self.variable.trace_remove("write", self._trace)
        except tk.TclError:
            pass
        super().destroy()


class RoundedEntry(tk.Frame):
    def __init__(self, master, textvariable, font=Fonts.ui, width=20) -> None:
        super().__init__(master, bg=master.cget("bg"), padx=11, pady=8)
        self._surface = tk.Label(self, bd=0, bg=self.cget("bg"))
        self._surface.place(x=0, y=0, relwidth=1, relheight=1, bordermode="outside")
        self._surface.lower()
        self.entry = tk.Entry(self, textvariable=textvariable, font=font, width=width,
                              bg=Theme.input_bg, fg=Theme.text, insertbackground=Theme.accent,
                              disabledbackground=Theme.input_bg, disabledforeground="#a0a0a8",
                              relief="flat", bd=0, highlightthickness=0)
        self.entry.pack(fill="x", expand=True)
        self.bind("<Configure>", self._paint, add="+")
        self.entry.bind("<FocusIn>", self._paint)
        self.entry.bind("<FocusOut>", self._paint)
        self._edit_menu = tk.Menu(self.entry, tearoff=False)
        for label, virtual in (("剪切", "<<Cut>>"), ("复制", "<<Copy>>"), ("粘贴", "<<Paste>>")):
            self._edit_menu.add_command(label=label,
                                        command=lambda event=virtual: self.entry.event_generate(event))
        self._edit_menu.add_separator()
        self._edit_menu.add_command(label="全选", command=lambda: self.entry.selection_range(0, "end"))
        self.entry.bind("<Button-3>", lambda event: self._edit_menu.tk_popup(event.x_root, event.y_root))
        self.entry.bind("<Control-a>", lambda _event: (self.entry.selection_range(0, "end"), "break")[-1])
        if sys.platform == "darwin":
            self.entry.bind("<Command-a>", lambda _event: (self.entry.selection_range(0, "end"), "break")[-1])

    def _paint(self, _event=None) -> None:
        width, height = max(2, self.winfo_width()), max(2, self.winfo_height())
        border = Theme.accent if self.focus_get() == self.entry else Theme.border
        self._photo = ImageTk.PhotoImage(
            rounded_image(width, height, Theme.input_bg, super().cget("bg"), radius=9, border=border), master=self)
        self._surface.configure(image=self._photo)
        self._surface.lower()

    def configure(self, cnf=None, **kwargs):
        if hasattr(self, "entry") and "state" in kwargs:
            self.entry.configure(state=kwargs.pop("state"))
        return super().configure(cnf, **kwargs)

    config = configure

    def cget(self, key):
        if key == "state" and hasattr(self, "entry"):
            return self.entry.cget(key)
        return super().cget(key)


class Badge(tk.Canvas):
    def __init__(self, master, text="就绪", fg=Theme.accent, bg=Theme.accent_soft) -> None:
        self._parent_bg = master.cget("bg")
        super().__init__(master, width=78, height=28, bg=self._parent_bg, highlightthickness=0)
        self.text, self.foreground, self.fill = text, fg, bg
        self._paint()

    def configure(self, cnf=None, **kwargs):
        for key, attr in (("text", "text"), ("fg", "foreground"), ("bg", "fill")):
            if key in kwargs:
                setattr(self, attr, kwargs.pop(key))
        result = super().configure(cnf, **kwargs)
        if hasattr(self, "text"):
            self._paint()
        return result

    config = configure

    def _paint(self) -> None:
        self._photo = ImageTk.PhotoImage(rounded_image(78, 28, self.fill, self._parent_bg, radius=14), master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")
        self.create_text(39, 14, text=self.text, fill=self.foreground, font=Fonts.small)


class ValueSlider(tk.Frame):
    """Keyboard accessible slider with a quiet track and a readable value."""
    def __init__(self, master, variable, minimum=15, maximum=100):
        super().__init__(master, bg=master.cget("bg"))
        self.variable, self.minimum, self.maximum = variable, minimum, maximum
        self.enabled = True
        self.value_label = tk.Label(self, textvariable=variable, width=3, bg=self.cget("bg"),
                                    fg=Theme.accent, font=Fonts.ui_bold)
        self.value_label.pack(side="right", padx=(12, 0))
        self.track = tk.Canvas(self, height=32, width=140, bg=self.cget("bg"),
                                highlightthickness=0, takefocus=True, cursor="hand2")
        self.track.pack(side="left", fill="x", expand=True)
        self.track.bind("<Configure>", self._paint)
        self.track.bind("<Button-1>", self._seek)
        self.track.bind("<B1-Motion>", self._seek)
        for key, step in (("Left", -1), ("Right", 1)):
            self.track.bind(f"<{key}>", lambda _e, delta=step: self._key(delta))
        self.track.bind("<Home>", lambda _e: self._set(self.minimum))
        self.track.bind("<End>", lambda _e: self._set(self.maximum))
        self.track.bind("<FocusIn>", self._paint)
        self.track.bind("<FocusOut>", self._paint)
        self._trace = variable.trace_add("write", self._paint)

    def _set(self, value):
        if self.enabled:
            self.variable.set(round(max(self.minimum, min(self.maximum, value))))
        return "break"

    def _key(self, delta):
        return self._set(self.variable.get()+delta)

    def _seek(self, event):
        if self.enabled:
            self.track.focus_set()
            self._set(self.minimum+(event.x-10)/max(1, self.track.winfo_width()-20)*(self.maximum-self.minimum))
        return "break"

    def _paint(self, *_args):
        self.track.delete("all")
        width = max(24, self.track.winfo_width())
        x = 10+(width-20)*(self.variable.get()-self.minimum)/(self.maximum-self.minimum)
        color = Theme.accent if self.enabled else Theme.muted
        self.track.create_line(10, 16, width-10, 16, fill=Theme.border, width=4, capstyle="round")
        self.track.create_line(10, 16, x, 16, fill=color, width=4, capstyle="round")
        if self.track.focus_get() == self.track:
            self.track.create_oval(x-11, 5, x+11, 27, outline=Theme.accent_soft, width=3)
        self.track.create_oval(x-7, 9, x+7, 23, fill=Theme.card, outline=color, width=2)

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self.enabled = kwargs.pop("state") != "disabled"
            self.track.configure(takefocus=self.enabled, cursor="hand2" if self.enabled else "arrow")
            self.value_label.configure(fg=Theme.accent if self.enabled else Theme.muted)
            self._paint()
        return super().configure(cnf, **kwargs)

    config = configure

    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()


class SlimProgressbar(tk.Canvas):
    def __init__(self, master, variable):
        super().__init__(master, height=6, highlightthickness=0, bg=master.cget("bg"))
        self.variable = variable
        self._trace = variable.trace_add("write", self._paint)
        self.bind("<Configure>", self._paint)

    def _paint(self, *_args):
        width = max(6, self.winfo_width())
        value = max(0., min(100., self.variable.get()))
        self.delete("all")
        self.create_line(3, 3, width-3, 3, width=6, fill=Theme.border, capstyle="round")
        if value > 0:
            self.create_line(3, 3, 3+(width-6)*value/100, 3, width=6,
                             fill=Theme.accent, capstyle="round")

    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()


class PlaybackTimeline(tk.Canvas):
    """A visible playback track with pointer and keyboard seeking."""

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, height=36, width=240, bg=master.cget("bg"),
                         highlightthickness=0, takefocus=True, **kwargs)
        self.maximum, self.value = 1.0, 0.0
        self.enabled = False
        self._dragging = False
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        for key in ("Left", "Right", "Home", "End"):
            self.bind(f"<{key}>", self._key)
        self._paint()

    def configure(self, cnf=None, **kwargs):
        maximum = kwargs.pop("to", None)
        result = super().configure(cnf, **kwargs)
        if maximum is not None:
            self.maximum = max(0.001, float(maximum))
            self.set(self.value)
        return result

    config = configure

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self._paint()

    def set(self, value: float) -> None:
        self.value = max(0.0, min(self.maximum, float(value)))
        self._paint()

    def get(self) -> float:
        return self.value

    def _paint(self) -> None:
        self.delete("all")
        left, right = 16, max(17, self.winfo_width() - 16)
        y = 18
        x = left + (right - left) * self.value / self.maximum
        accent = Theme.accent if self.enabled else "#a8b4c6"
        self.create_line(left, y, right, y, width=10, fill="#d6dce5",
                         capstyle="round", tags="track")
        if x > left:
            self.create_line(left, y, x, y, width=10, fill=accent,
                             capstyle="round", tags="played")
        self.create_oval(x - 15, y - 15, x + 15, y + 15, fill=Theme.accent_soft,
                         outline=accent if self.focus_get() == self else "", tags="halo")
        self.create_oval(x - 10, y - 10, x + 10, y + 10,
                         fill=accent, outline="white", width=2, tags="thumb")

    def _set_pointer(self, event) -> None:
        self.set((event.x - 16) / max(1, self.winfo_width() - 32) * self.maximum)

    def _press(self, event) -> None:
        if self.enabled:
            self.focus_set()
            self._dragging = True
            self._set_pointer(event)
            self.event_generate("<<SeekStart>>")
            self.event_generate("<<SeekPreview>>")

    def _drag(self, event) -> None:
        if self._dragging:
            self._set_pointer(event)
            self.event_generate("<<SeekPreview>>")

    def _release(self, event) -> None:
        if self._dragging:
            self._set_pointer(event)
            self._dragging = False
            self.event_generate("<<SeekCommit>>")

    def _key(self, event) -> str:
        if self.enabled:
            if event.keysym == "Home":
                self.set(0)
            elif event.keysym == "End":
                self.set(self.maximum)
            else:
                self.set(self.value + (5 if event.keysym == "Right" else -5))
            self.event_generate("<<SeekCommit>>")
        return "break"


class SlimScrollbar(tk.Canvas):
    def __init__(self, master, command, orient="vertical", bg=None,
                 troughcolor=None, activebackground=None, **kwargs) -> None:
        if orient != "vertical":
            raise ValueError("SlimScrollbar only supports vertical scrolling.")
        super().__init__(master, width=8, height=1, bg=troughcolor or bg or master.cget("bg"),
                         highlightthickness=0, **kwargs)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)

    def set(self, first, last) -> None:
        self.first, self.last = float(first), float(last)
        self._paint()

    def _paint(self) -> None:
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        height = max(1, self.winfo_height())
        top = max(4, self.first * height)
        bottom = min(height - 4, max(top + 16, self.last * height))
        self.create_line(4, top, 4, bottom, width=4, fill="#c5c5cc", capstyle="round")

    def _press(self, event) -> None:
        height = max(1, self.winfo_height())
        if not self.first * height <= event.y <= self.last * height:
            target = max(0.0, min(1.0, event.y / height - (self.last - self.first) / 2))
            self.command("moveto", target)
        self._drag_y, self._drag_first = event.y, self.first

    def _drag(self, event) -> None:
        target = self._drag_first + (event.y - self._drag_y) / max(1, self.winfo_height())
        self.command("moveto", max(0.0, min(1.0, target)))
