"""Drama genres and the two prompts built from them, following the video-workflow skill.

1. build_character_prompt(): the skill's fixed character-sheet template, sent to Seedream 5.0 Pro
   together with the user's photo. The sheet becomes the identity anchor for the video.
2. build_prompt(): the Seedance 2.5 prompt (style header, subject tag, shot-by-shot beats with
   camera tags, audio cues, negative constraints). Never write a double hyphen in a prompt: the
   model silently drops everything after it.
"""

MAX_PLOT_LEN = 200

# Text of video-workflow/scripts/prompt_templates/reference_image_prompt.md (kept in sync by hand).
CHARACTER_SHEET_TEMPLATE = (
    "Character sheet, turnaround, three views, full body, standing presentation. {idea}. "
    "Front view, side view, and back view. Crisp details, high resolution, clean solid white background. "
    "no watermark, no logo, no brand text, no on-screen text, no invented branding on clothing or props."
)

# Each genre: a setting, the lead's costume (fixed by the character sheet), and five 6-second shots.
# <Subject1> is the lead (the person in the photo), <Subject2> a supporting character.
GENRES = [
    {
        "id": "romance",
        "title": "Rainy Night Reunion",
        "tagline": "Sweet romance",
        "emoji": "💌",
        "setting": "A soft, neon-lit city at night in the rain. Warm romantic colour grade, shallow depth of field.",
        "costume": "a long camel trench coat over a soft cream knit sweater",
        "beats": [
            "[WS, slow push-in] <Subject1> stands under a bus-stop shelter in the rain, checking a phone, then looks up at someone off-screen and freezes in surprise. (soft piano begins) <rain on the shelter roof>",
            "[CU, static] <Subject1>'s face as recognition turns into a shaky smile. Dialogue (Subject1, whispering): {It's really you... after all these years.}",
            "[MS, over the shoulder] <Subject2>, an old friend, steps under the shelter holding an umbrella. The two stand close, rain drumming on the roof, neither speaking.",
            "[MCU, shallow focus] <Subject1> laughs through tears. Dialogue (Subject1, warm): {You still remember my favourite coffee?} Warm streetlight glows on their faces.",
            "[WS, slow pull-back] They walk away together under one umbrella, city lights blurring behind. Dialogue (Subject1, softly): {This time, let's not let go.} (the piano swells and fades)",
        ],
    },
    {
        "id": "heir",
        "title": "The Hidden Heir",
        "tagline": "Secret billionaire comeback",
        "emoji": "👑",
        "setting": "A glass-walled corporate tower and a grand ballroom. Crisp, high-contrast cinematic look, confident camera moves.",
        "costume": "a sharp tailored charcoal suit with a white shirt and no tie",
        "beats": [
            "[WS, handheld] <Subject1> is dismissed by arrogant executives in a glass boardroom. Dialogue (Subject2, sneering): {Security, escort this person out.}",
            "[CU, static] <Subject1> straightens up calmly. Dialogue (Subject1, low and steady): {Before you do, check who signed the building's deed.}",
            "[MS, quick cuts] The executives' faces drain of colour as a manager whispers in a panic and tablets light up around the table. <phones buzzing>",
            "[WS, slow-motion tracking] <Subject1> strides through the ballroom doors as the crowd parts and assistants bow slightly. (confident orchestral beat)",
            "[MS, low angle, slow push-in] <Subject1> at the head of the table, smiling. Dialogue (Subject1, confident): {Meeting begins now.} Freeze on a steady stare.",
        ],
    },
    {
        "id": "wuxia",
        "title": "Last Sword of the Mist",
        "tagline": "Martial arts epic",
        "emoji": "🗡️",
        "setting": "An ancient bamboo forest wrapped in mist, then a mountain temple courtyard. Painterly wuxia cinematography, flowing robes, wind and falling leaves.",
        "costume": "flowing ink-grey and indigo traveller's robes with a leather belt and a plain sheathed sword at the hip",
        "beats": [
            "[EWS, slow crane down] <Subject1> walks alone through drifting mist and bamboo, one hand resting on the sword hilt. <wind through bamboo>",
            "[CU, static] A masked swordmaster, <Subject2>, drops from above. <Subject1>'s eyes narrow. Dialogue (Subject1, calm): {You waited ten years for this duel?}",
            "[MS, tracking, slow motion] Stylised, non-graphic sword duel: blades clash in sparks, bamboo leaves swirl, robes whip in the wind. <steel ringing>",
            "[CU, static] <Subject1> spins and stops the final strike a hair's breadth from <Subject2>, who lowers the blade and bows.",
            "[WS, slow crane up] Sunrise over a mountain temple courtyard. <Subject1> sheathes the sword. Dialogue (Subject1, quiet): {The blade only protects.} (a gentle guqin melody)",
        ],
    },
    {
        "id": "thriller",
        "title": "The Missing Hour",
        "tagline": "Office mystery thriller",
        "emoji": "🕵️",
        "setting": "A nearly empty office at night, cold blue-green tones, tense sound design, handheld camera and sharp cuts.",
        "costume": "a dark navy blazer over a plain grey shirt",
        "beats": [
            "[WS, handheld] <Subject1> works late alone in a dim office. The wall clock ticks, the lights flicker, and a colleague's desk has a phone still buzzing and no owner. <clock ticking>",
            "[CU, static] <Subject1> finds a handwritten warning note under the keyboard. Dialogue (Subject1, whispering): {Who left this here?}",
            "[MS, tracking from behind] <Subject1> moves down a dim corridor as a shadow passes behind frosted glass. The elevator doors open on their own. <elevator chime>",
            "[MCU, whip pan] A hand grabs <Subject1>'s shoulder: it is the missing colleague, <Subject2>, breathless. Dialogue (Subject2, urgent): {They erased the last hour. Look at your watch.}",
            "[ECU, static] Extreme close-up of <Subject1>'s watch, an hour behind. <Subject1> looks up, stunned. Cut to black on a heartbeat sound. (a low tense drone)",
        ],
    },
    {
        "id": "timeslip",
        "title": "Time-Slip Café",
        "tagline": "Time travel fantasy",
        "emoji": "⏳",
        "setting": "A cosy retro café whose clocks glow gold. Dreamy, nostalgic film look with lens flares and floating dust particles.",
        "costume": "a vintage denim jacket over a cream sweater",
        "beats": [
            "[WS, slow push-in] <Subject1> steps into a tiny café on a rainy afternoon. A bell chimes and every clock on the wall spins backwards. <door bell> (a dreamy music box begins)",
            "[MS, slow pan to the window] The street outside turns into a sunny street from decades ago. <Subject1> touches the glass. Dialogue (Subject1, amazed): {What just happened?}",
            "[MCU, static] An old barista, <Subject2>, smiles knowingly and slides over a cup. Dialogue (Subject2, gentle): {Every guest gets one hour to change one moment.}",
            "[Montage, warm golden light] <Subject1> hugs a younger version of a loved one, both laughing and crying.",
            "[WS, slow pull-back] Back in the present, <Subject1> leaves the café lighter, smiling at the sky. Dialogue (Subject1, warm): {Some moments are worth going back for.}",
        ],
    },
    {
        "id": "palace",
        "title": "Empress of Ashes",
        "tagline": "Palace intrigue",
        "emoji": "🏯",
        "setting": "A lavish ancient imperial palace: red pillars, silk banners, lantern light. Rich saturated colours, dramatic symmetrical framing.",
        "costume": "elaborate embroidered imperial robes in crimson and gold with a jade hairpin",
        "beats": [
            "[WS, symmetrical, slow push-in] <Subject1> kneels alone in a vast candlelit hall while whispering courtiers watch from the shadows. (a low gong)",
            "[CU, static] <Subject1> raises their head, eyes calm. A minister, <Subject2>, makes an accusation. Dialogue (Subject1, cool): {Bring your proof to the court.}",
            "[MS, tracking] <Subject1> rises and walks down the long hall as the crowd parts. A hidden scroll is dropped at the throne's feet.",
            "[WS, slow orbit] The real schemer is exposed and falls to their knees. The court gasps. <Subject1> stands unmoved, sleeves fluttering. <gasps and rustling silk>",
            "[WS, low angle, slow crane up] <Subject1> on the palace steps at dusk with lanterns glowing. Dialogue (Subject1, resolute): {Power is not taken. It is earned.} (solemn strings)",
        ],
    },
    {
        "id": "revenge",
        "title": "Reborn to Rewrite",
        "tagline": "Rebirth revenge drama",
        "emoji": "🥀",
        "setting": "A glittering engagement banquet in a modern mansion, then a quiet bedroom at dawn. Glossy melodrama look, gold and deep red tones, dramatic push-ins.",
        "costume": "a sleek black evening outfit under a long tailored black coat",
        "beats": [
            "[WS, slow push-in] At a glittering banquet, <Subject1> watches their fiancé, <Subject2>, raise a glass beside a smirking rival as the guests laugh. A champagne glass slips from <Subject1>'s hand and shatters. <glass shattering> (tense strings)",
            "[CU, static, flash of white light] <Subject1> gasps awake in bed at dawn, touches their own face in the mirror, and realises it is three years earlier. Dialogue (Subject1, whispering): {I'm back... before everything.}",
            "[MS, tracking] <Subject1> walks into the same banquet, calm and composed, as the guests turn to stare. (a slow, confident beat)",
            "[MCU, over the shoulder] <Subject1> hands <Subject2> a sealed envelope, and <Subject2>'s smile freezes. Dialogue (Subject1, cool): {This time, I read the contract first.}",
            "[WS, slow pull-back] <Subject1> walks out into the night as the banquet falls into stunned whispers. Dialogue (Subject1, quiet): {Second chances are for me, not for you.} (the strings rise)",
        ],
    },
    {
        "id": "scifi",
        "title": "Last Signal from Orbit",
        "tagline": "Space sci-fi adventure",
        "emoji": "🚀",
        "setting": "A sleek space station orbiting Earth, then its observation deck. Cool blue and white lighting, weightless floating details, clean futuristic production design.",
        "costume": "a fitted white and slate-grey flight suit with a plain mission patch",
        "beats": [
            "[WS, slow drift] <Subject1> floats through a quiet space station corridor, Earth glowing in a round window, as red warning lights begin to pulse. <soft alarm beeping> (a low synth pad)",
            "[CU, static] <Subject1> presses a headset as a faint voice crackles through static. Dialogue (Subject1, focused): {Mission control, do you copy? Anyone?}",
            "[MS, handheld] <Subject1> pulls along a handrail to the control panel as sparks drift weightlessly, then reroutes cables with steady hands. <electric crackle>",
            "[MCU, slow push-in] Commander <Subject2> appears on the video screen, relieved. Dialogue (Subject2, breathless): {We thought we'd lost you. You just saved the whole station.}",
            "[EWS, slow pull-back] The station glides into sunrise over Earth while <Subject1> rests a hand on the window, smiling. Dialogue (Subject1, softly): {Bringing everyone home.} (the synth swells)",
        ],
    },
    {
        "id": "fantasy",
        "title": "The Dragon's Oath",
        "tagline": "Epic fantasy adventure",
        "emoji": "🐉",
        "setting": "An enchanted forest of glowing blue flowers, then a cliff above a misty valley at sunset. Lush high fantasy look, drifting magical particles, sweeping camera moves.",
        "costume": "a deep green hooded cloak over a brown leather tunic, with a simple silver amulet",
        "beats": [
            "[EWS, slow crane down] <Subject1> walks through an enchanted forest where glowing flowers light up at every step. <birdsong and wind chimes> (a soft flute melody)",
            "[CU, static] The silver amulet on <Subject1>'s chest begins to glow. Dialogue (Subject1, amazed): {It's calling to me.}",
            "[WS, low angle] A great silver dragon lands on the cliff ahead, folding its wings, its eyes gentle. The ground trembles. <deep rumbling breath>",
            "[MCU, over the shoulder] <Subject1> steps forward and rests a hand on the dragon's snout as golden light spreads between them. Dialogue (Subject1, steady): {I'm not afraid of you.}",
            "[WS, sweeping aerial] <Subject1> rides the dragon over a misty valley at sunset. Dialogue (Subject1, joyful): {Let's fly!} (the orchestra soars)",
        ],
    },
]

_BY_ID = {g["id"]: g for g in GENRES}


def get_genre(genre_id):
    return _BY_ID.get(genre_id)


def clean_plot(text):
    """Trim the optional user plot idea to a single short line."""
    text = " ".join(str(text or "").split())
    return text[:MAX_PLOT_LEN]


def build_character_prompt(genre, style):
    """Seedream prompt: the skill's character-sheet template around a description of the lead."""
    idea = (
        f"The person in the reference photo, styled as the lead of a {genre['tagline'].lower()} short drama, "
        f"wearing {genre['costume']}. Keep their face, hairstyle, skin tone and facial features exactly as in the "
        "reference photo"
    )
    return f"{CHARACTER_SHEET_TEMPLATE.format(idea=idea)} Art style: {style}."


def build_prompt(genre, plot, duration=30, ratio="9:16", style="Cinematic Realism"):
    """Seedance prompt: header, subject definition, shots with camera tags, negative constraints."""
    lines = [
        f"{style}, vertical {ratio}, {duration} seconds, with natural sound, music and spoken English dialogue.",
        "@image1 is the character sheet of <Subject1>, the lead (front, side and back views). Use it only as an "
        "identity reference: keep <Subject1>'s face, hairstyle, build and outfit exactly consistent in every shot, "
        "and never show the sheet, its white background or several views of <Subject1> at once.",
        f"Genre: {genre['title']} ({genre['tagline']}). {genre['setting']}",
    ]
    if plot:
        lines.append(f"Story idea from the viewer, weave it into the plot: {plot}")

    step = duration / len(genre["beats"])
    lines.append("Shot list:")
    for i, beat in enumerate(genre["beats"]):
        start, end = round(i * step), round((i + 1) * step)
        lines.append(f"Shot {i + 1} ({start}-{end}s): {beat}")

    lines.append(
        "no watermark, no logo, no subtitles, no on-screen text, no captions. Exactly one instance of <Subject1> "
        "is visible at any time: no duplicate characters, no twin subjects, no split screen. "
        "Wholesome tone, no nudity, no gore."
    )
    return "\n".join(lines)
