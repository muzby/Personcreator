import asyncio
import hashlib
import random
import re
import secrets
import shutil
import string

import aiofiles
import aiofiles.os
import aiohttp
import yaml

from datetime import date
from pydantic import BaseModel
from loguru import logger
from dataclasses import dataclass
from collections import Counter
from transliterate import translit


class Person(BaseModel):

    @dataclass
    class Gender(int):
        FEMALE = 1
        MALE = 2

    @dataclass
    class Lang(str):
        EN = 'en'
        RU = 'ru'

    phone: str | None
    first_name: str | None
    last_name: str | None
    gender: int = random.randint(1, 2)
    password: str | None
    usernames: list | None
    lang_code: str = random.choice(['ru', 'en'])
    birthdate: date


class PersonMaker:

    class VkUser(BaseModel):
        id: int
        photo_id: str
        first_name: str
        last_name: str
        is_closed: bool

    class Config(BaseModel):
        api_url: str
        proxy: str | None
        params: dict | None

    class Response(BaseModel):

        class Error(BaseModel):
            error_code: int
            error_msg: str

        class Data(BaseModel):
            count: int
            items: list

        response: Data | None
        error: Error | None

    def __init__(self, phone: str | int = None):
        config = self.Config(**yaml.load(stream=open('config.yaml', 'r'), Loader=yaml.Loader))
        self.params = config.params
        self.proxy = config.proxy
        self.phone = phone
        self.session = aiohttp.ClientSession(config.api_url)

    @staticmethod
    async def generate_password(person: Person = None, length: int = random.randint(7, 10)) -> Person | str:
        letters = string.ascii_letters
        digits = string.digits
        alphabet = letters + digits
        password = ''
        for _ in range(length):
            password += ''.join(secrets.choice(alphabet))
        if person:
            person.password = password
            return person
        return password

    @staticmethod
    async def generate_usernames(person: Person) -> Person | bool:
        usernames = list()
        prefix = len(person.first_name)
        prefix_list = list()
        postfix = len(person.last_name)
        postfix_list = list()
        for _ in range(-prefix+3, 0):
            prefix_list.append(person.first_name[:_])
        for _ in range(-postfix+3, 0):
            postfix_list.append(person.last_name[:_])
        for _ in prefix_list:
            for __ in postfix_list:
                if 'ru' in person.lang_code:
                    usernames.append(translit(f'{_}{__}', language_code='ru', reversed=True).replace("'", ''))
                    usernames.append(translit(f'{__}{_}', language_code='ru', reversed=True).replace("'", ''))
                    continue
                usernames.append(f'{_}{__}')
                usernames.append(f'{__}{_}')
        if len(usernames) > 0:
            person.usernames = usernames
            return person
        return False

    async def get_profile_photos(self, vk_user: VkUser, person: Person) -> Person | bool:
        method = '/method/photos.getProfile'
        owner_id = vk_user.id
        params = self.params
        params.update(owner_id=owner_id)
        async with self.session.get(method, params=params, proxy=self.proxy) as response:
            response = self.Response(**await response.json())
            if response.error:
                logger.warning(response.error.error_msg)
                return False
        photos_urls = list()
        for photo in response.response.items:
            photos_urls.append(photo['sizes'][-1]['url'])
        photos_urls = [*{*random.choices(photos_urls, k=4)}]
        counter = 1
        try:
            await aiofiles.os.makedirs(f'photos/{person.phone}/', exist_ok=False)
        except FileExistsError:
            shutil.rmtree(f'photos/{person.phone}/')
            await aiofiles.os.makedirs(f'photos/{person.phone}/', exist_ok=False)
        for photo_url in photos_urls:
            host = str(*re.findall(r'(https://.*.userapi.com)', photo_url))
            root = str(*re.findall(r'https://.*.userapi.com(/.*)', photo_url))
            async with aiohttp.ClientSession(host) as session:
                async with session.get(root, proxy=self.proxy) as response:
                    chunk_size = 1024
                    async with aiofiles.open(f'photos/{person.phone}/{counter}.jpeg', 'wb') as to_write:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            await to_write.write(chunk)
            counter += 1
        return person

    async def search_users(self, person: Person) -> Person | bool:
        method = '/method/users.search'
        params = self.params
        age = date.today().year - person.birthdate.year
        params.update(
            sort=random.randint(0, 1),
            count=1000,
            birth_day=person.birthdate.day,
            birth_month=person.birthdate.month,
            fields='photo_id',
            age_from=age,
            age_to=age,
            sex=person.gender,
            has_photo=1,
            lang=person.lang_code
        )
        async with self.session.get(method, params=params, proxy=self.proxy) as response:
            response = self.Response(**await response.json())
            if response.error:
                logger.warning(response.error.error_msg)
                return False
        not_closed = [
            PersonMaker.VkUser(**user) for user in response.response.items
            if user['is_closed'] is False and user.get('photo_id') and
            len(user['first_name']) > 3 and len(user['last_name']) > 3
        ]
        first_names = Counter([name.first_name for name in not_closed])
        last_names = Counter([name.last_name for name in not_closed])
        last_names = [k for k, v in last_names.items() if v > 2 and k not in first_names.keys()]
        if len(last_names) > 0:
            first_names = [k for k, v in first_names.items() if v > 5]
            if len(first_names) > 0:
                person.first_name = random.choice(first_names)
                person.last_name = random.choice(last_names)
            else:
                person.birthdate = date(
                    day=random.randint(1, 28),
                    month=random.randint(1, 12),
                    year=person.birthdate.year
                )
                await self.search_users(person)
        else:
            person.birthdate = date(
                day=random.randint(1, 28),
                month=random.randint(1, 12),
                year=person.birthdate.year
            )
            await self.search_users(person)
        if person.phone:
            person = await self.get_profile_photos(vk_user=random.choice(not_closed), person=person)
            return person
        md5_hash = hashlib.md5(f'{person.last_name}{person.first_name}{person.birthdate}'.encode())
        person.phone = md5_hash.hexdigest()
        person = await self.get_profile_photos(vk_user=random.choice(not_closed), person=person)
        return person

    async def generate(self, age: int = None, gender: int = None, lang_code: str = None) -> Person:
        person = dict()
        if self.phone:
            person.update(phone=self.phone)
        year = date.today().year - random.randint(18, 45)
        if age:
            year = date.today().year - age
        try:
            birthday = date(
                day=random.randint(1, 31),
                month=random.randint(1, 12),
                year=year
            )
        except ValueError:
            birthday = date(
                day=random.randint(1, 28),
                month=random.randint(1, 12),
                year=year
            )
        person.update(birthdate=birthday)
        if gender:
            person.update(gender=gender)
        if lang_code:
            person.update(lang_code=lang_code)
        person = Person(**person)
        if person:
            person = await self.search_users(person)
        if person:
            person = await self.generate_usernames(person)
        if person:
            person = await self.generate_password(person)
        await self.session.close()
        return person


async def main():
    person1 = await PersonMaker().generate(age=25, gender=Person.Gender.MALE)
    person2 = await PersonMaker().generate(age=30, gender=Person.Gender.FEMALE)
    person3 = await PersonMaker(phone=12534466).generate(age=35, gender=Person.Gender.FEMALE, lang_code=Person.Lang.RU)

if __name__ == '__main__':
    asyncio.run(main())
