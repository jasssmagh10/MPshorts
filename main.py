import os
import wave
import requests
from google import genai
from google.genai import types

# ─── CONFIGURATION (Reads from GitHub Secrets) ──────────────────────────────
API_KEY             = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID")

# Quick check to ensure secrets are configured in GitHub
if not all([API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise ValueError("❌ Missing environment variables. Check your GitHub Secrets.")

# ─── YOUR SCRIPT ───────────────────────────────────────────────────────────
full_script = """
The alarm goes off at 6:00 AM. A working-class father sits at the kitchen table with a stack of unopened mail. At the top of the pile is a credit card bill showing a balance of five thousand dollars. At twenty-four percent interest, the minimum payment barely covers the finance charges. To him, that bill isn't just numbers on paper. It’s weight. It’s anxiety. It’s the feeling of running on a treadmill that keeps speeding up while he gets more tired. To him, and to millions like him, debt is a trap—a quiet, constant tax on his future.

Now, shift the perspective across town. Inside a glass high-rise, a commercial real estate developer is signing papers on a thirty-million-dollar bank loan. He isn't sweating. His heart rate hasn't changed. In fact, he spent three months negotiating with banks specifically to get this loan.

Both men are using the exact same financial tool. Both have agreements with a bank to borrow money. Yet for one man, that tool is destroying his freedom. For the other, it’s building an empire.

The difference between these two worlds isn't luck, and it isn't raw intelligence. It’s a fundamental split in psychology. It’s how each group understands the mechanism of leverage.

When most people think of debt, they think of consumption. You want a car you can't afford, so you borrow money to drive it today. You want a vacation, a new television, or clothes for the weekend, so you swipe a plastic card. In the modern economy, consumer debt is structured to feel painless in the moment. It disconnects the pleasure of buying something from the pain of paying for it. But the math behind it is brutal.

When you borrow money to buy something that loses value, you aren't just paying for the item. You are paying for the item plus interest, using your future labor as collateral. Every dollar of high-interest consumer debt you take on is a contract promising that tomorrow, you will work harder for less money. It locks you into the present moment, forcing you to trade your most valuable asset—time—just to service yesterday’s decisions.
The wealthy look at that same mechanism and see something completely different.

To someone who understands finance, money isn't something you spend to buy things. Money is an employee. And debt is simply a way to hire more employees than you currently have cash to pay for.

When a wealthy investor takes on debt, they obey one strict rule: the asset bought with the borrowed money must generate a higher return than the cost of the loan itself. If a bank lends money at six percent, and the investor can put that money into an asset that yields ten percent, they haven't taken on a burden. They’ve manufactured a four percent margin out of thin air using someone else's capital.

Think about how that changes the math of life.

If you save up your own money to buy an apartment building, it might take you twenty years to buy one property. But if you use leverage correctly, you can acquire that same building today. The tenants pay the rent. That rent pays off the bank loan, covers the upkeep, and leaves a profit in your pocket. Ten years later, the bank is paid off, the property has appreciated in value, and you didn't use your own life savings to do it. The system paid for itself.

That is the hidden core of the wealth gap. One side buys liabilities that drain their cash flow every month, while the other side borrows to acquire income-producing assets that pay off the debt for them.

There’s another layer to this that rarely gets talked about: risk and survival.

When you live paycheck to paycheck, debt leaves zero margin for error. A sudden car repair or a medical emergency turns a small credit card balance into a spiral. The stress changes how your brain functions. It forces short-term decision-making. You can't plan ten years ahead when you're trying to figure out how to cover next Tuesday.

The wealthy use debt with safety nets built in. They don't gamble on hope; they structure loans with fixed rates, long time horizons, and underlying cash flow that buffers against market downturns. They also use debt to navigate the tax system legally. When you sell an asset for a profit, you get taxed. But when you borrow against an asset that has grown in value, that borrowed cash isn't considered income by the tax code. It’s tax-free liquidity. They can fund their lifestyle or make new investments without triggering a massive tax bill.

Debt itself is completely neutral. It’s neither good nor bad. It doesn't care who holds it or what their intentions are. It is simply an amplifier.

If you use it without a plan, on items that depreciate, it amplifies poverty. It accelerates how fast you give away your future. But if you understand how to direct it toward assets, cash flow, and equity, it amplifies growth faster than simple saving ever could.

The table in that kitchen and the glass room in that high-rise exist in the same economy. The rules of the game are written on the exact same paper. The only difference is knowing which side of the math you're standing on.
"""

# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────────
def wave_file(filename, pcm, channels=1, rate=24000, sample_width=2):
    """Writes raw PCM data to a proper WAV container."""
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

def merge_wavs(input_files, output_file):
    """Concatenates multiple WAV files into one master file."""
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
    """Uploads a document to Telegram using the Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with open(filepath, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"document": f},
            timeout=180,
        )
    if r.status_code == 200:
        print(f"  ✅ Sent {os.path.basename(filepath)} to Telegram")
    else:
        print(f"  ❌ Failed to send {os.path.basename(filepath)}: {r.status_code} {r.text}")

# ─── 1. GENERATE AUDIO CHUNKS ──────────────────────────────────────────────
client = genai.Client(api_key=API_KEY)

# Split script by double newlines (paragraphs), group them in pairs to stay within TTS token limits
paragraphs = [p.strip() for p in full_script.split("\n\n") if p.strip()]
chunks = ["\n\n".join(paragraphs[i:i + 2]) for i in range(0, len(paragraphs), 2)]

generated_files = []

for idx, chunk in enumerate(chunks, start=1):
    print(f"Reading chunk {idx} of {len(chunks)}...")
    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=f"Read aloud verbatim:\n\n{chunk}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Algenib")
                )
            ),
        ),
    )
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            fname = f"chunk_{idx}.wav"
            wave_file(fname, part.inline_data.data)
            generated_files.append(fname)

print("✅ Done! All audio chunks are generated.\n")

# ─── 2. MERGE INTO ONE MASTER WAV ──────────────────────────────────────────
MASTER = "master.wav"
print(f"Merging {len(generated_files)} chunks into {MASTER}...")
merge_wavs(generated_files, MASTER)
print(f"✅ Master file created: {MASTER}\n")

# ─── 3. SEND EVERYTHING TO TELEGRAM ────────────────────────────────────────
print("Uploading to Telegram...")
for f in generated_files:
    send_to_telegram(f, caption=f"Chunk: {f}")

send_to_telegram(MASTER, caption="🎧 Full master audio file")

print("\n🎉 All done!")