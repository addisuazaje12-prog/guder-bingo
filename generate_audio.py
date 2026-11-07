import os
from gtts import gTTS

# Amharic Number-to-Words Converter
units = ["", "አንድ", "ሁለት", "ሶስት", "አራት", "አምስት", "ስድስት", "ሰባት", "ስምንት", "ዘጠኝ"]
tens = ["", "አስር", "ሀያ", "ሰላሳ", "አርባ", "ሀምሳ", "ስልሳ", "ሰባ", "ሰማንያ", "ዘጠና"]

def get_amharic_words(num):
    if num <= 9:
        return units[num]
    if num == 10:
        return "አስር"
    if 10 < num < 20:
        return "አስራ " + units[num % 10]
    ten_part = num // 10
    unit_part = num % 10
    return tens[ten_part] if unit_part == 0 else f"{tens[ten_part]} {units[unit_part]}"

# Output folder for GitHub upload
output_dir = "bingo_amharic_audio"
os.makedirs(output_dir, exist_ok=True)

print("Starting generation of 76 Amharic audio files...")

# Generate b1.mp3 to o75.mp3
for num in range(1, 76):
    if num <= 15:
        prefix, am_letter = "b", "ቢ"
    elif num <= 30:
        prefix, am_letter = "i", "አይ"
    elif num <= 45:
        prefix, am_letter = "n", "ኤን"
    elif num <= 60:
        prefix, am_letter = "g", "ጂ"
    else:
        prefix, am_letter = "o", "ኦ"
        
    phrase = f"{am_letter} {get_amharic_words(num)}"
    filename = f"{prefix}{num}.mp3"
    filepath = os.path.join(output_dir, filename)
    
    tts = gTTS(text=phrase, lang='am')
    tts.save(filepath)
    print(f"Generated ({num}/75): {filename} -> '{phrase}'")

# Generate Winner Announcement (bingo.mp3)
bingo_phrase = "ቢንጎ! ቢንጎ! እንኳን ደስ አለዎት አሸናፊ ሆነዋል!"
bingo_filepath = os.path.join(output_dir, "bingo.mp3")
bingo_tts = gTTS(text=bingo_phrase, lang='am')
bingo_tts.save(bingo_filepath)
print("Generated (76/76): bingo.mp3 -> 'ቢንጎ! ቢንጎ!'")

print("\nSuccess! All 76 Amharic .mp3 files are saved inside the 'bingo_amharic_audio' folder.")