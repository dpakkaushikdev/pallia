"""Plain two-column signature appearance with a name on whole-word lines."""


def build_appearance_image(signer_name, timestamp_str, load_font):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (440, 92), "white")
    draw = ImageDraw.Draw(image)
    divider = 220
    words = signer_name.split()
    lines = ([" ".join(words[:-1]), words[-1]] if len(words) > 1
             else [signer_name or "Signatory"])
    lines = [line.upper() for line in lines]

    # Fit whole words inside the name column; never let the name cross into
    # the certificate details. Account for the font's actual ink bounds.
    for size in range(38, 5, -1):
        font = load_font("arial.ttf", size)
        bounds = [draw.textbbox((0, 0), line, font=font) for line in lines]
        heights = [box[3] - box[1] for box in bounds]
        total_height = sum(heights) + 8 * (len(lines) - 1)
        if max(box[2] - box[0] for box in bounds) <= divider - 20 and total_height <= 76:
            break
    y = (92 - total_height) / 2
    for line, box, height in zip(lines, bounds, heights):
        width = box[2] - box[0]
        draw.text(((divider - width) / 2 - box[0], y - box[1]),
                  line, font=font, fill="black")
        y += height + 8

    draw.line([(divider, 10), (divider, 82)], fill=(160, 160, 160), width=1)
    date, _, time = timestamp_str.partition(" ")
    details = ["Digitally signed by", signer_name, f"Date: {date}", time]
    for index, text in enumerate(details):
        for size in range(13, 5, -1):
            font = load_font("arialbd.ttf" if index == 0 else "arial.ttf", size)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= 200:
                break
        draw.text((232 - box[0], 6 + index * 20 - box[1]), text, font=font, fill="black")
    return image
