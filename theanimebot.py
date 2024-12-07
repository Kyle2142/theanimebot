#!/usr/bin/env python3

import asyncio
import configparser
import logging
import os
import re
import sys
from html import escape
from logging.handlers import RotatingFileHandler
from functools import partial

import aiohttp
import telethon
from telethon.tl.custom import Button
from telethon.tl.custom.inlinebuilder import InlineBuilder
from telethon.tl.types import InputWebDocument

try:
    import ujson as json
except ImportError:
    import json

escape = partial(escape, quote=False)

IN_DOCKER = os.getenv('DOCKER', False)
LOG_FILE = 'logs/bot.log'
RESULTS_PER_QUERY = 10


class Handler:
    http: aiohttp.client.ClientSession = None
    base_url = 'https://graphql.anilist.co'
    logger = logging.getLogger('handler')
    HTML_REGEX = re.compile(r'</?(p|br) ?/?>')
    # @formatter:off
    GENRES = {'action', 'adventure', 'comedy', 'drama', 'ecchi', 'fantasy', 'horror', 'mahou shoujo', 'mecha', 'music', 'mystery', 'psychological', 'romance', 'sci-fi', 'slice of life', 'sports', 'supernatural', 'thriller'}
    TAGS = {'4-koma', 'achronological order', 'acting', 'advertisement', 'afterlife', 'age gap', 'age regression', 'agender', 'airsoft', 'aliens', 'alternate universe', 'american football', 'amnesia', 'anachronism', 'animals', 'anthology', 'anti-hero', 'archery', 'artificial intelligence', 'asexual', 'assassins', 'astronomy', 'athletics', 'augmented reality', 'autobiographical', 'aviation', 'badminton', 'band', 'bar', 'baseball', 'basketball', 'battle royale', 'biographical', 'bisexual', 'body horror', 'body swapping', 'boxing', "boys' love", 'bullying', 'calligraphy', 'card battle', 'cars', 'centaur', 'cgi', 'cheerleading', 'chibi', 'chuunibyou', 'circus', 'classic literature', 'college', 'coming of age', 'conspiracy', 'cosmic horror', 'cosplay', 'crime', 'crossdressing', 'crossover', 'cult', 'cultivation', 'cute girls doing cute things', 'cyberpunk', 'cycling', 'dancing', 'death game', 'delinquents', 'demons', 'denpa', 'detective', 'dinosaurs', 'dissociative identities', 'dragons', 'drawing', 'drugs', 'dullahan', 'dungeon', 'dystopian', 'economics', 'educational', 'elf', 'ensemble cast', 'environmental', 'episodic', 'ero guro', 'espionage', 'fairy tale', 'family life', 'fashion', 'female protagonist', 'firefighters', 'fishing', 'fitness', 'flash', 'food', 'football', 'foreign', 'fugitive', 'full cgi', 'full color', 'gambling', 'gangs', 'gender bending', 'ghost', 'go', 'goblin', 'gods', 'golf', 'gore', 'guns', 'gyaru', 'harem', 'henshin', 'hikikomori', 'historical', 'ice skating', 'idol', 'isekai', 'iyashikei', 'josei', 'kaiju', 'karuta', 'kemonomimi', 'kids', 'lacrosse', 'language barrier', 'lgbtq issues', 'lost civilization', 'love triangle', 'mafia', 'magic', 'mahjong', 'maids', 'male protagonist', 'martial arts', 'medicine', 'memory manipulation', 'mermaid', 'meta', 'military', 'monster girl', 'mopeds', 'motorcycles', 'musical', 'mythology', 'nekomimi', 'ninja', 'no dialogue', 'noir', 'nudity', 'nun', 'office lady', 'oiran', 'otaku culture', 'outdoor', 'parody', 'philosophy', 'photography', 'pirates', 'poker', 'police', 'politics', 'post-apocalyptic', 'primarily adult cast', 'primarily child cast', 'primarily female cast', 'primarily male cast', 'puppetry', 'rakugo', 'real robot', 'rehabilitation', 'reincarnation', 'revenge', 'reverse harem', 'robots', 'rotoscoping', 'rugby', 'rural', 'samurai', 'satire', 'school', 'school club', 'seinen', 'ships', 'shogi', 'shoujo', 'shounen', 'shrine maiden', 'skeleton', 'slapstick', 'slavery', 'software development', 'space', 'space opera', 'steampunk', 'stop motion', 'succubus', 'super power', 'super robot', 'superhero', 'surfing', 'surreal comedy', 'survival', 'swimming', 'swordplay', 'table tennis', 'tanks', 'teacher', "teens' love", 'tennis', 'terrorism', 'time manipulation', 'time skip', 'tokusatsu', 'tragedy', 'trains', 'triads', 'tsundere', 'twins', 'urban', 'urban fantasy', 'vampire', 'video games', 'vikings', 'virtual world', 'volleyball', 'war', 'werewolf', 'witch', 'work', 'wrestling', 'writing', 'wuxia', 'yakuza', 'yandere', 'youkai', 'yuri', 'zombie'}
    # @formatter:on
    QUERY = '''
query ($id: Int, $page: Int, $perPage: Int, $search: String, $genres: [String], $tags: [String], $type: MediaType) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            total
            currentPage
            lastPage
            hasNextPage
            perPage
        }
        media (id: $id, search: $search, genre_in: $genres, type: $type, tag_in: $tags) {
            siteUrl, idMal,
            title {
                romaji, english, native
            },
            coverImage {
              large
            },
            episodes, seasonYear, description(asHtml: true), meanScore, format, countryOfOrigin, genres
        }
    }
}'''

    def __init__(self, config, bot):
        self.config = config
        self.cache_time = config['TG API'].getint('cache_time')
        self.builder = InlineBuilder(bot)

    @telethon.events.register(telethon.events.InlineQuery(pattern="^$"))
    async def inline_help(self, event: telethon.events.InlineQuery.Event):
        results = [
            await self.builder.article('Simple search', 'lucky star', text='X'),
            await self.builder.article('Search with genre/tag', 'romance: usagi', text='X'),
            await self.builder.article('Multiple genres/tags', 'action, mecha', text='X'),
            await self.builder.article('Supported tags/genres', 'Use the button above for a list of supported tags/genres', text='X')
        ]
        await event.answer(results, self.cache_time, switch_pm='Tap here for more help', switch_pm_param='help')

    @telethon.events.register(telethon.events.InlineQuery(pattern="(?i)(.+)"))
    async def inline_handler(self, event: telethon.events.InlineQuery.Event):
        offset = int(event.offset) if event.offset.isdigit() else 0
        next_offset = str(offset + RESULTS_PER_QUERY)

        terms = event.pattern_match.group(1).split(': ', 1)

        self.logger.debug('Inline query %s (offset=%s): %s', event.id, offset, terms)

        if len(terms) == 2:
            tags_or_genres, search = terms
        else:
            tags_or_genres, search = terms[0], None

        tokens = {x.lower() for x in tags_or_genres.replace(', ', ',').split(',')}
        genres = tuple(tokens.intersection(self.GENRES))
        tags = tuple(tokens.intersection(self.TAGS))

        if not genres and not tags:
            search = tags_or_genres.replace(',', ' ')  # none were found, use as search term

        body = {
            'query': self.QUERY,
            'variables': {
                'page': (offset // RESULTS_PER_QUERY) + 1, 'perPage': RESULTS_PER_QUERY,
                'type': 'ANIME', 'search': search, 'genres': genres, 'tags': tags
            }
        }

        for k, v in tuple(body['variables'].items()):
            if not v:
                body['variables'].pop(k)

        async with self.http.post(self.base_url, json=body) as resp:
            api_result = (await resp.json()) or {}
            api_result = (api_result.get('data') or {}).get('Page')

        if not api_result:
            await event.answer()

        if not api_result['pageInfo'].get('hasNextPage'):
            next_offset = None

        results = []
        for i, result in enumerate(api_result.get('media', tuple())):
            # manually unescape to avoid unexpected escapes
            d = self.HTML_REGEX.sub('', result['description']).replace('&quot;', '"')

            title = result['title']
            native = title.get('native')
            english = title.get('english')
            native_emoji = "🇯🇵" if result['countryOfOrigin'] == 'JP' else ''
            img = result['coverImage']['large']
            links = f"<a href='{result['siteUrl']}'>AniList</a>"
            if result['idMal']:
                links += f" | <a href='https://myanimelist.net/anime/{result['idMal']}'>MAL</a>"

            results.append(
                await self.builder.article(
                    title['romaji'], id=str(i), parse_mode='html',
                    thumb=InputWebDocument(img, 0, 'image/jpeg', []),
                    text=f"<a href=\"{escape(img)}\">\u200d</a>"
                         f"<b>{escape(title['romaji'])}</b>\n" +
                         (f"🇬🇧<b>{escape(english)}</b>\n" if english and english.lower() != title['romaji'].lower() else '') +
                         (f"{native_emoji}<b>{escape(native)}</b>\n" if native else '') +
                         f"\n<b>{result['format'].capitalize()}</b>: {result['episodes']} episodes (<b>aired</b>: {result['seasonYear']})\n"  # NB: \n at start is to separate titles
                         f"<b>Score</b>: {result['meanScore']}\n" +
                         (f"<b>Genres</b>: {', '.join(result['genres'])}\n" if result['genres'] else '') +
                         f"<b>Description</b>:\n{d}"
                         f"\n\n{links}"
                )
            )
        # protect against empty entities
        results[-1].send_message.entities = list(filter(lambda x: x.length, results[-1].send_message.entities))

        self.logger.debug("Inline query %d: Processed %d results", event.id, len(results))

        try:
            await event.answer(results, next_offset=next_offset, cache_time=self.cache_time)
        except telethon.errors.QueryIdInvalidError:
            pass
        except telethon.errors.RPCError:
            self.logger.warning("Inline QUERY %d: Sending results failed", event.id, exc_info=True)
        else:
            self.logger.debug("Inline QUERY %d: Complete", event.id)

    @telethon.events.register(telethon.events.NewMessage(pattern=r"(?i)/logs?"))
    async def send_logs(self, event: telethon.events.NewMessage.Event):
        if event.chat_id != self.config['main'].getint('owner telegram id'):  # cannot use from_users due to config undefined
            return
        if os.path.exists(LOG_FILE):
            await event.reply(file=LOG_FILE)
        else:
            await event.reply("No log file found")

    @telethon.events.register(telethon.events.NewMessage(pattern=r"(?i)/(start|help) ?(.+)?"))
    async def start_help(self, event: telethon.events.NewMessage.Event):
        cmd = event.pattern_match.group(1)
        args = event.pattern_match.group(2)

        if cmd == 'start' and not args:
            await event.reply(
                "Hello! I am meant to be used in inline mode."
                "\nIf you are not sure what that means, try typing <code>@theanimebot</code> and a space or use the button below."
                "\nYou can also tap /help for more info on how to use me",
                parse_mode='HTML', buttons=[[Button.switch_inline('Try out inline mode', 'Amagami SS')]]
            )
            return

        await event.reply(
            "Inline mode usage:\n"
            "@theanimebot [comma-separated list of genres or tags][: ][search query]\n"
            "Examples:\n"
            "- Search for more than one tag or genre:\n"
            "   @theanimebot aliens, mecha\n"
            "- Search for a tag and some text:\n"
            "   @theanimebot romance: april\n"
            "- Search without genres or tags:\n"
            "   @theanimebot your lie in april\n\n"
            f"Supported tags: tap this -> /tags\n"
            f"Supported genres: tap this -> /genres"
        )

    @telethon.events.register(telethon.events.NewMessage(pattern=r"(?i)/(tag|genre)s?"))
    async def tags_genres(self, event: telethon.events.NewMessage.Event):
        key = event.pattern_match.group(1)
        if key == 'tag':
            items = self.TAGS
        elif key == 'genre':
            items = self.GENRES
        else:
            return

        temp = {}
        for item in items:  # index by first character
            temp.setdefault(item[0], []).append(item)

        content = '\n'.join([f"<b>{k.upper()}</b>:\n{', '.join(v)}" for k, v in sorted(temp.items())])
        await event.reply(f"Supported {key}s:\n{content}", parse_mode='HTML')

    @classmethod
    async def _create_session(cls):
        cls.http = aiohttp.client.ClientSession(json_serialize=json.dumps)


async def main(bot, config):
    await Handler._create_session()
    await bot.connect()
    if not await bot.is_user_authorized() or not await bot.is_bot():
        await bot.start(bot_token=config['TG API']['bot_token'])
    logging.info('Started bot')
    await bot.run_until_disconnected()


def setup():
    if not os.path.exists('config.ini'):
        raise FileNotFoundError('config.ini not found. Please copy example-config.ini and edit the relevant values')
    config = configparser.ConfigParser()
    config.read_file(open('config.ini'))

    logger = logging.getLogger()
    level = getattr(logging, config['main']['logging level'], logging.INFO)
    formatter = logging.Formatter("%(asctime)s\t%(levelname)s:%(message)s")
    logger.setLevel(level)

    if not os.path.exists('logs'):
        os.mkdir('logs', 0o770)
    logger.addHandler(logging.handlers.RotatingFileHandler(LOG_FILE, encoding='utf-8', maxBytes=5 * 1024 * 1024, backupCount=5))
    if IN_DOCKER:  # we are in docker, use stdout as well
        logger.addHandler(logging.StreamHandler(sys.stdout))

    for h in logger.handlers:
        h.setFormatter(formatter)
        h.setLevel(level)

    bot = telethon.TelegramClient(config['TG API']['session'],
                                  config['TG API'].getint('api_id'), config['TG API']['api_hash'],
                                  auto_reconnect=True, connection_retries=1000, flood_sleep_threshold=5)

    handler = Handler(config, bot)
    for f in (getattr(handler, h) for h in dir(handler) if not h.startswith('_') and callable(getattr(handler, h))):
        bot.add_event_handler(f)

    try:
        asyncio.get_event_loop().run_until_complete(main(bot, config))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    setup()
