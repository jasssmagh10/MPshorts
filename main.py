import os
import re
import time
import wave
import requests
from google import genai
from google.genai import types

# ─── CONFIGURATION (Reads from GitHub Secrets) ──────────────────────────────
API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ─── TTS SETTINGS ──────────────────────────────────────────────────────────
TTS_MODEL = "gemini-2.5-flash-preview-tts"   # Change to "gemini-3.8-flash-tts" if preferred
TTS_VOICE = "Algenib"
CHUNK_MAX_WORDS = 100
SLEEP_BETWEEN_CHUNKS = 5

# Style prompt — kept short and direct so the TTS model doesn't get confused
TTS_STYLE_PROMPT = (
    "Speak softly, with quiet contemplation and a heavy, grounded tone. "
    "Use a measured, deliberate pace. Hold noticeable pauses after key statements. "
    "Convey subtle weariness and quiet observation. Do not sound like a lecturer."
)

if not all([API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("❌ Missing environment variables. Check your GitHub Secrets.")

# ─── YOUR SCRIPT ───────────────────────────────────────────────────────────
full_script = """
There is a very specific sound to a pocket that only holds lint and loose change. It is a quiet, empty rustle of cheap cotton when you press your hands deep against your thighs just to look like you belong somewhere. You learn to read rooms by the floorboards. You know which ones creak under sudden weight and which ones stay silent. When you live in the margins, you develop a strange kind of peripheral vision. You notice the way a host's eyes flick down to your shoes before deciding whether a table is available. You notice the microscopic pause before someone shakes your hand, as if calculating the precise social cost of the contact.

Then the math changes.

It does not happen in a cinematic flash of lightning. It arrives in quiet bank statements and wire confirmations that take a few seconds to process on a glowing screen. But the outside world notices long before you do. The first shift is subtle. It lives in the posture of the people who used to look past your shoulder. The clerk behind the counter who once memorized your face as a mild security risk suddenly finds your jokes genuinely amusing. Your name gets pronounced correctly on the first try. The phone calls you make no longer go to voicemail; they get answered on the second ring by someone whose voice sounds smoothed down by expensive private school.

You start to notice that doors are no longer heavy. People rush to hold them, apologizing for the slight draft they might let in. Invitations arrive in crisp envelopes or through encrypted messaging apps from people who forgot your email address three years ago. They want your opinion on art, on real estate, on markets you barely understand. They lean in when you speak, nodding with a synchronized seriousness that implies your most casual observation contains hidden geometry.

You test them sometimes. You say something utterly mundane, a half-formed thought about the weather or a poorly cooked steak, and watch three people nod thoughtfully as if deciphering scripture. That is when the peculiar loneliness sets in.

The people who knew you when your car smelled like stale coffee and desperation treat you differently, too. Some pull away, not out of malice, but out of a sudden, bruising self-consciousness. They start checking the prices on the right side of the menu before they order, even when you tell them dinner is covered. They laugh differently at your stories, turning old inside jokes into formal performances. The easy, bruising friction of old friendships wears smooth, replaced by a polite, fragile glass floor. You cannot jump anymore. You cannot argue about small sums of money or share bad news without casting a long, uncomfortable shadow across the table.

New people arrive constantly. They orbit like planets caught in a sudden gravity well. They offer praise that tastes like sugar water, sweet for a second, then entirely hollow. They anticipate your needs before you voice them. They laugh at the exact right cadence. But behind their eyes, there is a frantic, quiet calculation. They are trying to figure out what kind of key you hold, which door you might unlock, and whether your proximity will rub off on them.

You begin to realize that elite circles are not built on warmth. They are built on frictionless performance. Everyone wears the same invisible armor. The tailoring is better, the watches cost as much as a small sedan, and the vocabulary is trimmed of all regional accents, but the underlying anxiety is identical to the one you felt in the back of the bus years ago. The only difference is the scale of the ledger. They are terrified of losing their footing on the glass, just like you used to be terrified of the concrete.

You stop explaining yourself. That is the real marker. When you are broke, you spend half your waking hours defending your choices, proving your worth, and begging the room to believe you matter. When you reach the other side, you realize silence is a luxury good. You let sentences trail off. You let awkward pauses stretch out for ten seconds because you no longer feel the desperate urge to fill them with your own defense.

The strangest part is looking in the mirror while wearing a suit that costs more than your first car. You still remember the exact taste of tap water when the utilities were about to be shut off. You still feel the phantom weight of a bounced check pressing against your ribs. The numbers in the account have changed, the geography of your life has expanded, and the people around you treat you like a monument instead of a person. Yet, beneath the polished surface, the wiring remains the same. You just learned how to keep the lights on without making a sound.
"""

# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────────
def wave_file(filename, pcm, channels=1, rate=24000, sample_width=2):
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

def merge_wavs(input_files, output_file):
    if not input_files:
        return
    with wave.open(input_files[0], "rb") as first:
        params = first.getparams()
    with wave.open(output_file, "wb") as out:
        out.setparams(params)
        for f in input_files:
            with wave.open(f, "rb") as wf:
                out.writeframes(wf.readframes(wf.getnframes()))

def send_to_telegram(filepath, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            files = {"document": (os.path.basename(filepath), f)}
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files=files,
                timeout=300,
            )
        if r.status_code == 200:
            print(f"  ✅ Sent {os.path.basename(filepath)} to Telegram")
        else:
            print(f"  ❌ Failed to send {os.path.basename(filepath)}: {r.status_code} {r.text}")
    except Exception as e:
        print(f"  ❌ Telegram exception for {os.path.basename(filepath)}: {e}")

def chunk_script_by_sentences(text, max_words=100):
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current_chunk, current_word_count = [], [], 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        word_count = len(sentence.split())
        if current_word_count + word_count > max_words and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sentence]
            current_word_count = word_count
        else:
            current_chunk.append(sentence)
            current_word_count += word_count
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def generate_tts_chunk(chunk_text, use_style=True):
    """Generates TTS for one chunk. Returns PCM bytes or raises an exception."""
    if use_style:
        contents = f"{TTS_STYLE_PROMPT}\n\nRead the following text aloud exactly as written:\n\n{chunk_text}"
    else:
        contents = f"Read aloud verbatim:\n\n{chunk_text}"

    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                )
            ),
        ),
    )

    # Safety check: some models return None content when rejected
    if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
        raise Exception("Model returned empty response")

    for part in response.candidates[0].content.parts:
        if part.inline_data:
            return part.inline_data.data

    raise Exception("Model returned no audio data")

# ─── 1. CHUNK THE SCRIPT ───────────────────────────────────────────────────
chunks = chunk_script_by_sentences(full_script, max_words=CHUNK_MAX_WORDS)
print(f"📝 Script split into {len(chunks)} chunks of ~{CHUNK_MAX_WORDS} words each.")
print(f"   Using model: {TTS_MODEL} | Voice: {TTS_VOICE}\n")

# ─── 2. GENERATE AUDIO CHUNKS ──────────────────────────────────────────────
client = genai.Client(api_key=API_KEY)
generated_files = []

for idx, chunk in enumerate(chunks, start=1):
    print(f"🎙️ Reading chunk {idx} of {len(chunks)}...")
    try:
        pcm = generate_tts_chunk(chunk, use_style=True)
    except Exception as e:
        print(f"   ⚠️ Styled TTS failed ({e}). Retrying without style prompt...")
        pcm = generate_tts_chunk(chunk, use_style=False)

    fname = f"chunk_{idx}.wav"
    wave_file(fname, pcm)
    generated_files.append(fname)
    print(f"   ✅ Chunk {idx} generated.")

    if idx < len(chunks):
        print(f"   ... Waiting {SLEEP_BETWEEN_CHUNKS}s ...")
        time.sleep(SLEEP_BETWEEN_CHUNKS)

print("\n✅ Done! All audio chunks are generated.\n")

# ─── 3. MERGE INTO ONE MASTER WAV ──────────────────────────────────────────
MASTER = "master.wav"
print(f"🔗 Merging {len(generated_files)} chunks into {MASTER}...")
merge_wavs(generated_files, MASTER)

with wave.open(MASTER, "rb") as f:
    duration = f.getnframes() / float(f.getframerate())
print(f"✅ Master file created: {MASTER} (Duration: {duration:.2f}s)\n")

# Clean up chunk files
for f in generated_files:
    try:
        os.remove(f)
    except:
        pass

# ─── 4. SEND MASTER TO TELEGRAM ────────────────────────────────────────────
print("📤 Uploading master audio to Telegram...")
send_to_telegram(MASTER, caption=f"🎧 Full master audio ({duration:.1f}s) — {TTS_MODEL} / {TTS_VOICE}")

print("\n🎉 All done!")