from PIL import Image
import sys


SCREEN_WIDTH = 256
SCREEN_HEIGHT = 192
BITMAP_SIZE = 6144
ATTR_SIZE = 768
SCR_SIZE = BITMAP_SIZE + ATTR_SIZE


# ZX Spectrum palette.
# Two variants: normal brightness and bright.
PALETTE_NORMAL = [
    (0, 0, 0),        # black
    (0, 0, 205),      # blue
    (205, 0, 0),      # red
    (205, 0, 205),    # magenta
    (0, 205, 0),      # green
    (0, 205, 205),    # cyan
    (205, 205, 0),    # yellow
    (205, 205, 205),  # white
]

PALETTE_BRIGHT = [
    (0, 0, 0),        # black
    (0, 0, 255),      # blue
    (255, 0, 0),      # red
    (255, 0, 255),    # magenta
    (0, 255, 0),      # green
    (0, 255, 255),    # cyan
    (255, 255, 0),    # yellow
    (255, 255, 255),  # white
]


def zx_bitmap_offset(x_byte: int, y: int) -> int:
    """
    Return byte offset in the first 6144 bytes of a ZX Spectrum .scr bitmap.

    x_byte is in range 0..31.
    y is in range 0..191.
    """
    return ((y & 0xC0) << 5) | ((y & 0x07) << 8) | ((y & 0x38) << 2) | x_byte


def scr_to_image(scr_data: bytes, flash_phase: bool = False) -> Image.Image:
    """
    Convert 6912-byte ZX Spectrum .scr data to a PIL RGB image.

    flash_phase:
        If True, attributes with FLASH bit set will swap ink and paper.
        This can be used for animation.
    """
    if len(scr_data) != SCR_SIZE:
        raise ValueError(f"Expected {SCR_SIZE} bytes, got {len(scr_data)} bytes")

    image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT))
    pixels = image.load()

    for y in range(SCREEN_HEIGHT):
        attr_y = y // 8

        for x_byte in range(32):
            bitmap_index = zx_bitmap_offset(x_byte, y)
            bitmap_byte = scr_data[bitmap_index]

            attr_x = x_byte
            attr_index = BITMAP_SIZE + attr_y * 32 + attr_x
            attr = scr_data[attr_index]

            ink = attr & 0b00000111
            paper = (attr >> 3) & 0b00000111
            bright = bool(attr & 0b01000000)
            flash = bool(attr & 0b10000000)

            if flash and flash_phase:
                ink, paper = paper, ink

            palette = PALETTE_BRIGHT if bright else PALETTE_NORMAL

            ink_color = palette[ink]
            paper_color = palette[paper]

            for bit in range(8):
                x = x_byte * 8 + bit

                # ZX Spectrum stores leftmost pixel in bit 7.
                mask = 0b10000000 >> bit
                color = ink_color if bitmap_byte & mask else paper_color

                pixels[x, y] = color

    return image


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scr_viewer.py input.scr [output.png]")
        raise SystemExit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) >= 3 else None

    with open(input_path, "rb") as f:
        scr_data = f.read()

    image = scr_to_image(scr_data)

    if output_path:
        image.save(output_path)
        print(f"Saved {output_path}")
    else:
        image.show()


if __name__ == "__main__":
    main()