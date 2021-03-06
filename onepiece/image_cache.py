import re
import os
import hashlib
import time
import shutil
import warnings
from concurrent.futures import ThreadPoolExecutor

import requests

from .exceptions import ImageDownloadError


HERE = os.path.abspath(os.path.dirname(__file__))


def retry(times=3, delay=0):
    def _wrapper1(func):
        def _wrapper2(*args, **kwargs):
            i = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    i += 1
                    if i > times:
                        raise e
                    time.sleep(delay)
        return _wrapper2
    return _wrapper1


def walk(rootdir):
    for parent, dirnames, filenames in os.walk(rootdir):
        for filename in filenames:
            yield os.path.join(parent, filename)


class ImageCache():
    CACHE_DIR_NAME = "image_cache"
    CACHE_DIR = os.path.abspath(os.path.join(HERE, os.path.pardir, ".cache", CACHE_DIR_NAME))
    URL_PATTERN = re.compile(r'^https?://.*')
    EXPIRE = 10 * 24 * 60 * 60   # 缓存有效期 10 天
    _session = None
    IS_USE_CACHE = True

    IMAGE_DOWNLOAD_POOL = None
    DEFAULT_POOL_SIZE = 4

    @classmethod
    def get_session(cls):
        if cls._session is None:
            headers = {
                'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                               'Chrome/65.0.3325.146 Safari/537.36'),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*",
                "DNT": "1"


            }
            cls._session = requests.Session()
            cls._session.headers.update(headers)
        return cls._session

    @staticmethod
    def calc_str_md5(s):
        return hashlib.md5(s.encode()).hexdigest()

    @classmethod
    def get_pool(cls):
        if cls.IMAGE_DOWNLOAD_POOL is None:
            cls.IMAGE_DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=cls.DEFAULT_POOL_SIZE)
        return cls.IMAGE_DOWNLOAD_POOL

    @classmethod
    def set_cache_dir(cls, cache_dir):
        cls.CACHE_DIR = os.path.abspath(os.path.join(cache_dir, cls.CACHE_DIR_NAME))
        os.makedirs(cache_dir, exist_ok=True)

    @classmethod
    def url_to_path(cls, image_path_or_url):
        if not cls.URL_PATTERN.match(image_path_or_url):
            return image_path_or_url
        s = cls.calc_str_md5(image_path_or_url)
        return os.path.join(cls.CACHE_DIR, s[0:2], s[2:4], s[4:6], s)

    @classmethod
    @retry(times=3, delay=1)
    def download_image_without_cache(cls, image_url, target_path):
        try:
            session = cls.get_session()
            response = session.get(image_url)
            if response.status_code != 200:
                msg = '图片下载失败: status_code={} image_url={}'.format(response.status_code, image_url)
                raise ImageDownloadError(msg)
        except Exception as e:
            msg = "图片下载失败: image_url={} error: {}".format(image_url, e)
            raise ImageDownloadError(msg) from e

        image_dir = os.path.dirname(target_path)
        os.makedirs(image_dir, exist_ok=True)
        with open(target_path, 'wb') as f:
            f.write(response.content)
        return target_path

    @classmethod
    def download_image_use_cache(cls, image_url, target_path=None):
        cache_path = cls.url_to_path(image_url)
        if not os.path.exists(cache_path):
            cls.download_image_without_cache(image_url=image_url, target_path=cache_path)

        if target_path is None:
            return cache_path
        else:
            target_dir = os.path.dirname(target_path)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            shutil.copyfile(cache_path, target_path)
            return target_path

    @classmethod
    def download_image(cls, image_url, target_path):
        if cls.IS_USE_CACHE:
            return cls.download_image_use_cache(image_url=image_url, target_path=target_path)
        else:
            return cls.download_image_without_cache(image_url=image_url, target_path=target_path)

    @classmethod
    def download_images(cls, image_urls, output_dir):
        """下载出错只打印出警告信息，不抛出异常
        """
        pool = cls.get_pool()
        future_list = []
        for idx, image_url in enumerate(image_urls, start=1):
            ext = cls.find_suffix(image_url)
            target_path = os.path.join(output_dir.rstrip(), "{}.{}".format(idx, ext))
            future = pool.submit(cls.download_image, image_url=image_url, target_path=target_path)
            future_list.append(future)

        # 等全部图片下载完成
        for future in future_list:
            try:
                future.result()
            except Exception as e:
                warnings.warn(str(e))
        return output_dir

    @classmethod
    def auto_clean(cls):
        """
        清除超过有效期的文件
        """
        now = time.time()
        for file_path in walk(cls.CACHE_DIR):
            if now - os.path.getctime(file_path) > cls.EXPIRE:
                os.remove(file_path)

    @classmethod
    def remove_cache(cls):
        try:
            shutil.rmtree(cls.CACHE_DIR)
        except Exception as e:
            warnings.warn(str(e))

    @staticmethod
    def find_suffix(image_url, default='jpg', allow=frozenset(['jpg', 'png', 'jpeg', 'gif'])):
        """从图片url提取图片扩展名
        :param image_url: 图片链接
        :param default: 扩展名不在 allow 内，则返回默认扩展名
        :param allow: 允许的扩展名
        :return ext: 扩展名，不包含.
        """
        ext = image_url.rsplit('.', 1)[-1].lower()
        if ext not in allow:
            return default
        return ext
