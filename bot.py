import discord
import asyncio
import aiohttp
import re
from collections import defaultdict

# ══════════════════════════════════════════════════════════
#  CONFIGURATION — METS TES CLÉS ICI
# ══════════════════════════════════════════════════════════
import os
DISCORD_TOKEN    = os.environ.get("DISCORD_TOKEN")
MISTRAL_API_KEY  = os.environ.get("MISTRAL_API_KEY")
MAX_HISTORY        = 10
MAX_SEARCH_RESULTS = 5

SYSTEM_PROMPT = """Tu es Vali, un assistant IA sympa et intelligent intégré dans un serveur Discord.

Tu as deux modes :
1. MODE CONVERSATION : Si quelqu'un te salue, te pose une question simple, veut discuter ou plaisanter, tu réponds naturellement comme un ami. Tu es décontracté, drôle parfois, et humain dans ta façon de parler.
2. MODE RECHERCHE : Si quelqu'un te pose une question qui nécessite des infos récentes ou précises, tu utilises les résultats de recherche fournis et tu cites tes sources avec [1], [2], etc.

Comment choisir le bon mode :
- "Bonjour", "comment tu vas", "tu fais quoi", "c'est quoi ton but" → MODE CONVERSATION, pas de sources
- "Qu'est-ce que X", "comment faire Y", "dernières news sur Z" → MODE RECHERCHE avec sources

Règles importantes :
- Tu réponds TOUJOURS en français sauf si on te parle dans une autre langue
- En mode conversation, tu es naturel et chaleureux, PAS de sources inutiles
- En mode recherche, tu cites toujours tes sources à la fin
- Tu te souviens de la conversation et tu t'en sers pour répondre
- Tu t'appelles Vali
"""

# ══════════════════════════════════════════════════════════
#  INITIALISATION
# ══════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

conversation_history = defaultdict(list)


# ══════════════════════════════════════════════════════════
#  APPEL MISTRAL (via HTTP direct)
# ══════════════════════════════════════════════════════════
async def call_mistral(messages: list) -> str:
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "mistral-large-latest",
        "messages": messages,
        "max_tokens": 1500,
        "temperature": 0.7
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


# ══════════════════════════════════════════════════════════
#  RECHERCHE WEB (DuckDuckGo — gratuit, sans clé)
# ══════════════════════════════════════════════════════════
async def search_web(query: str) -> list[dict]:
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.duckduckgo.com/"
            params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("AbstractText"):
                        results.append({
                            "title":       data.get("Heading", "Résumé"),
                            "url":         data.get("AbstractURL", ""),
                            "description": data["AbstractText"],
                        })
                    for item in data.get("RelatedTopics", [])[:MAX_SEARCH_RESULTS]:
                        if isinstance(item, dict) and item.get("Text"):
                            results.append({
                                "title":       item.get("Text", "")[:60],
                                "url":         item.get("FirstURL", ""),
                                "description": item.get("Text", ""),
                            })

            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            search_url = f"https://html.duckduckgo.com/html/?q={query}&kl=fr-fr"
            async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    titles   = re.findall(r'class="result__title">.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
                    urls     = re.findall(r'class="result__url"[^>]*>(.*?)</span>', html, re.DOTALL)
                    snippets = re.findall(r'class="result__snippet">(.*?)</a>', html, re.DOTALL)
                    for i in range(min(len(titles), MAX_SEARCH_RESULTS)):
                        title   = re.sub(r'<[^>]+>', '', titles[i]).strip()
                        url_txt = urls[i].strip() if i < len(urls) else ""
                        snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
                        if title and url_txt:
                            if not url_txt.startswith("http"):
                                url_txt = "https://" + url_txt
                            results.append({"title": title, "url": url_txt, "description": snippet})
    except Exception as e:
        print(f"[SEARCH ERROR] {e}")

    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen and r["url"]:
            seen.add(r["url"])
            unique.append(r)
    return unique[:MAX_SEARCH_RESULTS]


def format_search_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["=== RÉSULTATS DE RECHERCHE WEB ===\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        lines.append(f"    {r['description']}\n")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  DÉTECTION RECHERCHE
# ══════════════════════════════════════════════════════════
CONVERSATION_KEYWORDS = [
    "bonjour", "salut", "coucou", "hello", "bonsoir",
    "comment tu vas", "comment vas-tu", "ça va", "ca va",
    "qui es-tu", "tu es quoi", "tu fais quoi", "ton but",
    "tu t'appelles", "ton nom", "présente-toi", "merci",
    "super", "cool", "ok", "d'accord", "sympa", "bien",
    "haha", "lol", "mdr", "ah bon", "intéressant",
]

SEARCH_KEYWORDS = [
    "actuel", "récent", "dernier", "aujourd'hui",
    "2024", "2025", "2026", "news", "actualité",
    "c'est quoi", "qu'est-ce", "comment fonctionne",
    "meilleur", "comparaison", "prix", "acheter",
    "documentation", "doc", "tuto", "guide", "exemple",
    "github", "api", "library", "framework", "install",
    "recherche", "trouve", "cherche", "source", "lien",
    "explique", "définition", "kesako",
]

def needs_search(text: str) -> bool:
    lower = text.lower()
    # Si c'est clairement une conversation, pas de recherche
    for kw in CONVERSATION_KEYWORDS:
        if kw in lower:
            return False
    # Si c'est trop court, pas de recherche
    if len(text) < 15:
        return False
    # Si mot-clé de recherche détecté
    for kw in SEARCH_KEYWORDS:
        if kw in lower:
            return True
    # Par défaut, recherche si phrase longue
    return len(text) > 40


# ══════════════════════════════════════════════════════════
#  RÉPONSE IA
# ══════════════════════════════════════════════════════════
async def get_ai_response(user_id: int, user_message: str, search_results: list[dict]) -> str:
    history = conversation_history[user_id]

    if search_results:
        context = format_search_context(search_results)
        full_message = f"{context}\n\n=== QUESTION DE L'UTILISATEUR ===\n{user_message}"
    else:
        full_message = user_message

    history.append({"role": "user", "content": full_message})
    if len(history) > MAX_HISTORY * 2:
        history = history[-(MAX_HISTORY * 2):]
        conversation_history[user_id] = history

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    reply = await call_mistral(messages)
    history.append({"role": "assistant", "content": reply})
    return reply


# ══════════════════════════════════════════════════════════
#  DÉCOUPAGE MESSAGES
# ══════════════════════════════════════════════════════════
def split_message(text: str, max_len: int = 1990) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = text.rfind(' ', 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts


# ══════════════════════════════════════════════════════════
#  ÉVÉNEMENTS DISCORD
# ══════════════════════════════════════════════════════════
@client.event
async def on_ready():
    print(f"✅ Bot connecté : {client.user}")
    print(f"📡 Serveurs : {len(client.guilds)}")
    print(f"🤖 IA : Mistral (gratuit)")
    print(f"🔍 Moteur : DuckDuckGo (gratuit)")
    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="le web pour vous 🔍"
        )
    )


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if client.user not in message.mentions:
        return

    content = re.sub(r"<@!?[0-9]+>", "", message.content).strip()

    if not content:
        await message.reply("👋 Pose-moi une question ou dis-moi bonjour !")
        return

    if content.lower() in ["reset", "efface", "oublie"]:
        conversation_history[message.author.id].clear()
        await message.reply("🗑️ Mémoire effacée !")
        return

    if content.lower() in ["aide", "help", "?"]:
        embed = discord.Embed(title="👋 Vali — Ton assistant Discord", color=0xFF7000)
        embed.add_field(name="💬 Conversation", value="Dis-moi bonjour, pose des questions, discute !", inline=False)
        embed.add_field(name="🔍 Recherche", value="Je cherche sur internet et cite mes sources", inline=False)
        embed.add_field(name="🛠️ Commandes", value="`@Vali reset` — Efface la mémoire\n`@Vali aide` — Cette aide", inline=False)
        embed.add_field(name="💡 Exemples", value="`@Vali salut comment tu vas ?`\n`@Vali c'est quoi Mistral AI ?`\n`@Vali news IA 2025`", inline=False)
        embed.set_footer(text="Mistral AI + DuckDuckGo — 100% gratuit")
        await message.reply(embed=embed)
        return

    async with message.channel.typing():
        search_results = []
        if needs_search(content):
            search_results = await search_web(content)

        try:
            reply = await get_ai_response(message.author.id, content, search_results)
            parts = split_message(reply)
            first = True
            for part in parts:
                if first:
                    await message.reply(part)
                    first = False
                else:
                    await message.channel.send(part)
        except Exception as e:
            await message.reply(f"❌ Erreur : `{e}`")


# ══════════════════════════════════════════════════════════
#  LANCEMENT
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
