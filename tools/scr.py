import sys
import tkinter as tk
from PIL import ImageTk, Image
from scr_viewer import scr_to_image

ZX_WIDTH = 256
ZX_HEIGHT = 192
FRAME_MS = 20*32

class ScrViewer(tk.Tk):
    def __init__(self, scr_path: str):
        super().__init__()

        self.title("ZX Spectrum SCR Viewer")
        self.geometry("512x384")
        self.minsize(ZX_WIDTH, ZX_HEIGHT)

        with open(scr_path, "rb") as f:
            self.scr_data = f.read()

        # Předpočítané dvě fáze FLASH.
        self.image_normal = scr_to_image(self.scr_data, flash_phase=False)
        self.image_flash = scr_to_image(self.scr_data, flash_phase=True)

        self.tk_image = None
        self.current_scale = None
        self.current_flash_phase = False

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.bind("<Configure>", self.on_resize)
        self.bind_all("<Escape>", self.close)
        self.bind_all("<space>", self.close)
        self.after(0, self.focus_force)

        self.after(0, self.redraw)
        self.after(FRAME_MS, self.video_tick)

    def close(self, event=None) -> None:
        self.destroy()

    def on_resize(self, event) -> None:
        if event.widget is self:
            self.redraw(force=True)

    def video_tick(self) -> None:
        """
        Simulace televizního vertikálního pulsu.

        Na reálném ZX Spectru by se toto dělo každých 20 ms.
        Čítač má 5 bitů, FLASH fáze je odvozena z bitu 4.
        """
        self.current_flash_phase = not self.current_flash_phase
        self.redraw(force=True)

        self.after(FRAME_MS, self.video_tick)

    def redraw(self, force: bool = False) -> None:
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        if canvas_width <= 1 or canvas_height <= 1:
            return

        scale = min(
            canvas_width // ZX_WIDTH,
            canvas_height // ZX_HEIGHT,
        )

        scale = max(1, scale)

        if not force and scale == self.current_scale:
            self.center_image()
            return

        self.current_scale = scale

        source_image = (
            self.image_flash
            if self.current_flash_phase
            else self.image_normal
        )

        new_width = ZX_WIDTH * scale
        new_height = ZX_HEIGHT * scale

        resized = source_image.resize(
            (new_width, new_height),
            Image.Resampling.NEAREST,
        )

        self.tk_image = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        self.canvas.create_image(
            canvas_width // 2,
            canvas_height // 2,
            image=self.tk_image,
            anchor=tk.CENTER,
            tags="scr_image",
        )

        self.title(
            f"ZX Spectrum SCR Viewer — {scale}× "
            f"({new_width}×{new_height}) "
            f"FLASH={int(self.current_flash_phase)}"
        )

    def center_image(self) -> None:
        self.canvas.coords(
            "scr_image",
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
        )


if __name__ == "__main__":
    app = ScrViewer(sys.argv[1])
    app.mainloop()
