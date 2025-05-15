from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Union
import requests
import math
import json
import redis
import time
from datetime import datetime
from enum import Enum

# Инициализация приложения FastAPI
app = FastAPI(
    title="LTC Exchange API",
    description="API для получения данных о биржах, торгующих Litecoin (LTC)",
    version="1.0.0"
)

# Настройка CORS для доступа с фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация подключения к Redis
redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
CACHE_TTL = 180  # время жизни кэша - 3 минуты

# Обновляем класс перечисления для поддержки возможных критериев сортировки
class SortCriterion(str, Enum):
    ID = "id"  # Добавляем новый критерий сортировки по ID
    PRICE = "price"
    VOLUME = "volume"
    PLUS_DEPTH = "plus_depth"
    MINUS_DEPTH = "minus_depth"
    EXCHANGE = "exchange"
    VOLUME_PERCENTAGE = "volume_percentage"  # Добавляем сортировку по проценту объема

# Модели данных для типизации и документации
class ExchangeData(BaseModel):
    id: int
    exchange: str
    pair: str
    price: str
    price_percent: Optional[float] = None  # Добавляем поле для процентной корректировки
    plusTwoPercentDepth: str
    minusTwoPercentDepth: str
    volume24h: str
    volumePercentage: str
    lastUpdated: str
    icon: Optional[str] = None  # Дополнительное поле для иконки биржи
    url: Optional[str] = None  # Добавляем поле для URL биржи

class ExchangeResponse(BaseModel):
    status: str
    data: List[ExchangeData]

class DepthData(BaseModel):
    exchange: str
    currentPrice: float
    plus2PercentDepth: str
    minus2PercentDepth: str

class DepthResponse(BaseModel):
    status: str
    data: DepthData

# Глобальное хранилище для пользовательских бирж
custom_exchanges: Dict[str, ExchangeData] = {}

class CustomExchangeInput(BaseModel):
    exchange: str
    pair: str = "LTC/USDT"
    price_percent: Optional[float] = None  # Добавляем поле для процентной корректировки
    plusTwoPercentDepth: float
    minusTwoPercentDepth: float
    volume24h: float
    volumePercentage: float
    icon: Optional[str] = None
    url: Optional[str] = None  # Добавляем поле для URL биржи

class CustomExchangeUpdateInput(BaseModel):
    pair: Optional[str] = None
    price: Optional[float] = None
    price_percent: Optional[float] = None  # Добавляем поле для процентной корректировки
    plusTwoPercentDepth: Optional[float] = None
    minusTwoPercentDepth: Optional[float] = None
    volume24h: Optional[float] = None
    volumePercentage: Optional[float] = None
    icon: Optional[str] = None
    url: Optional[str] = None  # Добавляем поле для URL биржи

@app.post("/api/custom-exchanges", tags=["exchanges"])
async def add_custom_exchange(exchange_data: CustomExchangeInput):
    """
    Добавляет или обновляет пользовательскую биржу с указанными данными.
    Биржа будет отображаться в общем списке при запросе всех бирж.
    """
    global custom_exchanges
    
    exchange_id = exchange_data.exchange.lower()
    
    print(exchange_data)
    # Если указан процент, рассчитываем цену автоматически
    price = None
    if exchange_data.price_percent is not None:
        binance_price = await get_binance_ltc_price()
        if binance_price > 0:
            price = binance_price * (1 + exchange_data.price_percent / 100)
    
    custom_exchanges[exchange_id] = ExchangeData(
        id=0,  # ID будет присвоен позже при объединении списков
        exchange=exchange_data.exchange,
        pair=exchange_data.pair,
        price=f"{price:.4f}" if price else "0.0000",
        price_percent=exchange_data.price_percent,  # Сохраняем процентную корректировку
        plusTwoPercentDepth=f"${math.floor(exchange_data.plusTwoPercentDepth):,}",
        minusTwoPercentDepth=f"${math.floor(exchange_data.minusTwoPercentDepth):,}",
        volume24h=f"${math.floor(exchange_data.volume24h):,}",
        volumePercentage=f"{exchange_data.volumePercentage:.2f}%",
        lastUpdated='Recently',
        icon=exchange_data.icon,
        url=exchange_data.url  # Добавляем URL биржи
    )
    
    return {
        "status": "success",
        "message": f"Биржа {exchange_data.exchange} успешно добавлена/обновлена"
    }

@app.get("/api/custom-exchanges", tags=["exchanges"])
async def get_custom_exchanges():
    """
    Возвращает список пользовательских бирж.
    """
    return {
        "status": "success",
        "data": list(custom_exchanges.values())
    }

@app.delete("/api/custom-exchanges/{exchange_name}", tags=["exchanges"])
async def delete_custom_exchange(exchange_name: str):
    """
    Удаляет пользовательскую биржу по имени.
    """
    global custom_exchanges
    
    exchange_id = exchange_name.lower()
    if exchange_id in custom_exchanges:
        del custom_exchanges[exchange_id]
        return {
            "status": "success",
            "message": f"Биржа {exchange_name} успешно удалена"
        }
    else:
        raise HTTPException(status_code=404, detail=f"Биржа {exchange_name} не найдена")

@app.patch("/api/custom-exchanges/{exchange_name}", tags=["exchanges"])
async def update_custom_exchange(exchange_name: str, exchange_data: CustomExchangeUpdateInput):
    """
    Обновляет отдельные параметры пользовательской биржи.
    Обновляются только те поля, которые указаны в запросе.
    """
    global custom_exchanges
    
    exchange_id = exchange_name.lower()
    if exchange_id not in custom_exchanges:
        raise HTTPException(status_code=404, detail=f"Биржа {exchange_name} не найдена")
    
    # Получаем текущие данные о бирже
    exchange = custom_exchanges[exchange_id]
    
    # Обновляем поля, которые были предоставлены
    if exchange_data.pair is not None:
        exchange.pair = exchange_data.pair
        
    # Особая обработка для процентной корректировки
    if exchange_data.price_percent is not None:
        exchange.price_percent = exchange_data.price_percent
        # Обновляем цену на основе новой процентной корректировки
        binance_price = await get_binance_ltc_price()
        if binance_price > 0:
            calculated_price = binance_price * (1 + exchange_data.price_percent / 100)
            exchange.price = f"{calculated_price:.4f}"
    elif exchange_data.price is not None:
        # Если указана конкретная цена, обнуляем процентную корректировку
        exchange.price = f"{exchange_data.price:.4f}"
        exchange.price_percent = None
        
    if exchange_data.plusTwoPercentDepth is not None:
        exchange.plusTwoPercentDepth = f"${math.floor(exchange_data.plusTwoPercentDepth):,}"
        
    if exchange_data.minusTwoPercentDepth is not None:
        exchange.minusTwoPercentDepth = f"${math.floor(exchange_data.minusTwoPercentDepth):,}"
        
    if exchange_data.volume24h is not None:
        exchange.volume24h = f"${math.floor(exchange_data.volume24h):,}"
        
    if exchange_data.volumePercentage is not None:
        exchange.volumePercentage = f"{exchange_data.volumePercentage:.2f}%"
        
    if exchange_data.icon is not None:
        exchange.icon = exchange_data.icon
    
    if exchange_data.url is not None:
        exchange.url = exchange_data.url
    
    # Обновляем временную метку
    exchange.lastUpdated = 'Recently'
    
    return {
        "status": "success",
        "message": f"Биржа {exchange_name} успешно обновлена",
        "data": exchange
    }

@app.get("/api/ltc-exchanges", response_model=ExchangeResponse, tags=["exchanges"])
async def get_ltc_exchanges(
    sort_by: Optional[SortCriterion] = None,
    descending: bool = True
):
    """
    Получает список бирж, торгующих парой LTC/USDT с возможностью сортировки по различным параметрам.
    
    - **sort_by**: Критерий сортировки (id, price, volume, plus_depth, minus_depth, exchange, volume_percentage)
    - **descending**: Порядок сортировки (по умолчанию - по убыванию)
    """
    try:
        # Проверяем наличие кеша базовых данных (без сортировки)
        base_cache_key = "ltc_exchanges_base_data"
        base_cached_data = redis_client.get(base_cache_key)
        
        # Проверяем наличие данных с текущими параметрами сортировки
        sort_cache_key = f"ltc_exchanges_data:{sort_by}:{descending}"
        sorted_cached_data = redis_client.get(sort_cache_key)
        
        # Если есть данные с запрошенной сортировкой, возвращаем их сразу
        if sorted_cached_data:
            print(f"CACHE HIT: Данные с сортировкой получены из кэша Redis с ключом {sort_cache_key}")
            return json.loads(sorted_cached_data)
        
        print(f"CACHE MISS: Данные с сортировкой не найдены в кэше Redis с ключом {sort_cache_key}")
        
        # Если есть базовые данные, используем их без повторного вызова API
        if base_cached_data:
            print(f"CACHE HIT: Используем базовые данные из кэша Redis для сортировки")
            result_data = json.loads(base_cached_data)
            exchanges = []
            
            # Преобразуем сырые данные в объекты ExchangeData
            for exchange_dict in result_data['data']:
                exchange = ExchangeData(**exchange_dict)
                exchanges.append(exchange)
            
            print(f"DEBUG: Загружено {len(exchanges)} бирж из базового кеша для сортировки")
        else:
            # Если базовых данных нет, получаем их из API и сохраняем
            print(f"CACHE MISS: Базовые данные не найдены в кэше Redis, получаем из API")
            exchanges = await fetch_exchange_data_from_api()
            
            # Сохраняем базовые данные в кеш
            base_result = {
                'status': 'success',
                'data': [exchange.__dict__ for exchange in exchanges]
            }
            try:
                redis_client.setex(base_cache_key, CACHE_TTL, json.dumps(base_result))
                print(f"DEBUG: Базовые данные успешно сохранены в кэш Redis с ключом {base_cache_key}")
            except Exception as cache_error:
                print(f"DEBUG: Ошибка при сохранении базовых данных в кэш: {str(cache_error)}")
        
        # Применяем сортировку
        print(f"DEBUG: Применяем сортировку к кешированным данным")
        print(f"DEBUG: Присвоены ID для {len(exchanges)} бирж")
        
        # Выполняем сортировку в зависимости от параметров
        if sort_by:
            print(f"DEBUG: Сортировка по критерию: {sort_by}, по убыванию: {descending}")
            if sort_by == SortCriterion.ID:
                # Сортировка по ID
                exchanges.sort(key=lambda x: x.id, reverse=descending)  
                print(f"DEBUG: Выполнена сортировка по ID")
            elif sort_by == SortCriterion.PRICE:
                exchanges.sort(key=lambda x: float(x.price.replace(',', '')), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по цене")
            elif sort_by == SortCriterion.VOLUME:
                exchanges.sort(key=lambda x: float(x.volume24h.replace('$', '').replace(',', '')), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по объему")
            elif sort_by == SortCriterion.PLUS_DEPTH:
                exchanges.sort(key=lambda x: float(x.plusTwoPercentDepth.replace('$', '').replace(',', '')), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по глубине +2%")
            elif sort_by == SortCriterion.MINUS_DEPTH:
                exchanges.sort(key=lambda x: float(x.minusTwoPercentDepth.replace('$', '').replace(',', '')), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по глубине -2%")
            elif sort_by == SortCriterion.EXCHANGE:
                exchanges.sort(key=lambda x: x.exchange.lower(), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по названию биржи")
            elif sort_by == SortCriterion.VOLUME_PERCENTAGE:
                exchanges.sort(key=lambda x: float(x.volumePercentage.replace('%', '')), reverse=descending)
                print(f"DEBUG: Выполнена сортировка по проценту объема")
        else:
            # По умолчанию сортируем по объему торгов
            exchanges.sort(key=lambda x: float(x.volume24h.replace('$', '').replace(',', '')), reverse=True)
            print(f"DEBUG: Выполнена сортировка по умолчанию (по объему, по убыванию)")
        
        # После сортировки, переназначаем ID чтобы они соответствовали новому порядку
        for i, exchange in enumerate(exchanges, start=1):
            exchange.id = i
        print(f"DEBUG: ID назначены после сортировки")
        
        # Выводим информацию о первых и последних элементах после сортировки для проверки
        if exchanges:
            first_exchange = exchanges[0]
            last_exchange = exchanges[-1]
            print(f"DEBUG: Первая биржа после сортировки: ID={first_exchange.id}, {first_exchange.exchange}, цена={first_exchange.price}, объем={first_exchange.volume24h}")
            print(f"DEBUG: Последняя биржа после сортировки: ID={last_exchange.id}, {last_exchange.exchange}, цена={last_exchange.price}, объем={last_exchange.volume24h}")
                
        result = {
            'status': 'success',
            'data': exchanges
        }
        
        # Сохраняем отсортированные данные в кэш
        print(f"CACHE SET: Сохраняем отсортированные данные в Redis с ключом {sort_cache_key} и TTL {CACHE_TTL} секунд")
        try:
            redis_client.setex(sort_cache_key, CACHE_TTL, json.dumps(result, default=lambda o: o.__dict__))
            print(f"DEBUG: Отсортированные данные успешно сохранены в кэш Redis")
        except Exception as cache_error:
            print(f"DEBUG: Ошибка при сохранении отсортированных данных в кэш: {str(cache_error)}")
        
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при получении данных по LTC: {str(e)}")

# Выделяем получение данных из API в отдельную функцию
async def fetch_exchange_data_from_api():
    """
    Получает данные о биржах из API CoinGecko и обрабатывает их
    """
    # Получаем список бирж для сопоставления иконок и URL
    exchanges_response = requests.get("https://api.coingecko.com/api/v3/exchanges")
    exchange_icon_mapping = {}
    exchange_url_mapping = {}
    if exchanges_response.status_code == 200:
        exchanges_data = exchanges_response.json()
        print(f"DEBUG: Получено {len(exchanges_data)} бирж из API exchanges")
        for ex in exchanges_data:
            exchange_icon_mapping[ex["id"]] = ex.get("image")
            exchange_url_mapping[ex["id"]] = ex.get("url")
    else:
        print(f"DEBUG: Ошибка API exchanges: {exchanges_response.status_code}, {exchanges_response.text[:200]}")
    
    # Хардкод иконок для бирж, которые отсутствуют в API или имеют проблемы с сопоставлением
    hardcoded_icons = {
        "bitstorage": "https://coin-images.coingecko.com/markets/images/394/small/Group_3575807.png?1706864409",
        "bcex": "https://coin-images.coingecko.com/markets/images/190/small/bcex.jpg?1706864323",
        "trade_ogre": "https://coin-images.coingecko.com/markets/images/101/small/tradeogre.jpeg?1706864289",
        "oceanex": "https://coin-images.coingecko.com/markets/images/341/small/Oceanex.png?1706864383",
        "probit": "https://coin-images.coingecko.com/markets/images/370/small/probit.png?1706864390",
        "grovex": "https://coin-images.coingecko.com/markets/images/11852/small/GroveX_200px.png?1738737388",
        "poloniex": "https://coin-images.coingecko.com/markets/images/37/small/poloniex.png?1706864269",
        "toko_crypto": "https://coin-images.coingecko.com/markets/images/501/small/toko.png?1706864476",
        "cex": "https://coin-images.coingecko.com/markets/images/56/small/main-icon.png?1706864277",
        "hitbtc": "https://coin-images.coingecko.com/markets/images/25/small/hitbtc.png",
        "coincatch": "https://coin-images.coingecko.com/markets/images/1214/small/CoinCatch_New_Logo.jpeg?1729059088"
    }
    
    # Добавляем хардкод иконок в общий маппинг
    exchange_icon_mapping.update(hardcoded_icons)
    
    # Получаем данные о Litecoin с CoinGecko
    response = requests.get('https://api.coingecko.com/api/v3/coins/litecoin/tickers')
    if response.status_code != 200:
        print(f"DEBUG: Ошибка API tickers: {response.status_code}, {response.text[:200]}")
        raise HTTPException(status_code=response.status_code, 
                            detail=f"Ошибка API CoinGecko: {response.text}")
    
    data = response.json()
    exchanges = []
    
    print(f"DEBUG: Получено {len(data['tickers'])} тикеров, фильтруем по USDT")
    usdt_tickers_count = 0
    
    for ticker in data['tickers']:
        # Фильтруем только пары LTC/USDT
        if ticker['target'] == 'USDT':
            usdt_tickers_count += 1
            base_volume_usd = ticker['converted_volume'].get('usd', 0)
            
            # Расчет значений глубины ордеров (примерные расчеты)
            plus_two_percent_depth = math.floor(base_volume_usd * 0.06)
            minus_two_percent_depth = math.floor(base_volume_usd * 0.05)
            
            # Получаем информацию о бирже и сопоставляем с иконкой и URL
            market_info = ticker.get('market', {})
            exchange_identifier = market_info.get('identifier')
            exchange_name = market_info.get('name', 'Unknown')
            
            # Пытаемся найти иконку и URL по идентификатору
            icon_url = exchange_icon_mapping.get(exchange_identifier)
            exchange_url = exchange_url_mapping.get(exchange_identifier)
            
            # Простая отладочная информация
            if icon_url:
                print(f"DEBUG: Биржа '{exchange_name}' (id: {exchange_identifier}): иконка найдена")
            else:
                print(f"DEBUG: ⚠️ Биржа '{exchange_name}' (id: {exchange_identifier}): иконка НЕ найдена!")
            
            exchange_data = ExchangeData(
                id=0,  # Временный ID, переназначим позже
                exchange=exchange_name,
                pair='LTC/USDT',
                price=f"{float(ticker['last']):.4f}",
                plusTwoPercentDepth=f"${plus_two_percent_depth:,}",
                minusTwoPercentDepth=f"${minus_two_percent_depth:,}",
                volume24h=f"${math.floor(base_volume_usd):,}",
                volumePercentage=f"{ticker.get('bid_ask_spread_percentage', 1.0):.2f}%",
                lastUpdated='Recently',
                icon=icon_url,
                url=exchange_url
            )
            
            exchanges.append(exchange_data)
    
    print(f"DEBUG: Обработано {usdt_tickers_count} USDT тикеров")
    
    # Добавляем пользовательские биржи к основному списку
    custom_exchange_count = len(custom_exchanges)
    print(f"DEBUG: Добавляем {custom_exchange_count} пользовательских бирж")
    for custom_exchange in custom_exchanges.values():
        # Обновляем цену для бирж с процентной корректировкой
        if custom_exchange.price_percent is not None:
            binance_price = await get_binance_ltc_price()
            if binance_price > 0:
                calculated_price = binance_price * (1 + custom_exchange.price_percent / 100)
                price_str = f"{calculated_price:.4f}"
            else:
                price_str = custom_exchange.price
        else:
            price_str = custom_exchange.price
        
        # Копируем данные, чтобы избежать изменения оригинального объекта
        exchange_copy = ExchangeData(
            id=0,  # Временный ID, переназначим позже
            exchange=custom_exchange.exchange,
            pair=custom_exchange.pair,
            price=price_str,  # Используем обновленную цену
            price_percent=custom_exchange.price_percent,
            plusTwoPercentDepth=custom_exchange.plusTwoPercentDepth,
            minusTwoPercentDepth=custom_exchange.minusTwoPercentDepth,
            volume24h=custom_exchange.volume24h,
            volumePercentage=custom_exchange.volumePercentage,
            lastUpdated=custom_exchange.lastUpdated,
            icon=custom_exchange.icon,
            url=custom_exchange.url if hasattr(custom_exchange, 'url') else None
        )
        exchanges.append(exchange_copy)
    
    # Присваиваем начальные ID всем биржам
    for i, exchange in enumerate(exchanges, start=1):
        exchange.id = i
    
    return exchanges

@app.get("/api/ltc-exchanges-cmc", response_model=ExchangeResponse, tags=["exchanges"])
async def get_ltc_exchanges_cmc():
    """
    Альтернативный маршрут для получения данных через CoinMarketCap API.
    Требует API-ключ от CoinMarketCap.
    """
    try:
        # Вам потребуется API-ключ от CoinMarketCap
        CMC_API_KEY = 'ВАШ_API_КЛЮЧ'
        
        headers = {
            'X-CMC_PRO_API_KEY': CMC_API_KEY
        }
        
        params = {
            'symbol': 'LTC',
            'convert': 'USD'
        }
        
        response = requests.get(
            'https://pro-api.coinmarketcap.com/v1/cryptocurrency/market-pairs/latest',
            headers=headers,
            params=params
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, 
                                detail=f"Ошибка API CoinMarketCap: {response.text}")
        
        data = response.json()
        exchanges = []
        index = 1
        
        for pair in data['data']['market_pairs']:
            if pair['market_pair_quote']['symbol'] == 'USDT':
                quote_volume = pair['quote']['USD']['volume_24h']
                
                exchange_data = ExchangeData(
                    id=index,
                    exchange=pair['exchange']['name'],
                    pair='LTC/USDT',
                    price=f"{float(pair['quote']['USD']['price']):.4f}",
                    plusTwoPercentDepth=f"${math.floor(quote_volume * 0.05):,}",
                    minusTwoPercentDepth=f"${math.floor(quote_volume * 0.04):,}",
                    volume24h=f"${math.floor(quote_volume):,}",
                    volumePercentage="1.23%",  # Заглушка - замените на реальные данные
                    lastUpdated='Recently'
                )
                
                exchanges.append(exchange_data)
                index += 1
        
        exchanges.sort(key=lambda x: float(x.volume24h.replace('$', '').replace(',', '')), reverse=True)
        top_10_exchanges = exchanges[:10]
        
        return {
            'status': 'success',
            'data': top_10_exchanges
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, 
                            detail=f"Ошибка при получении данных по LTC через CoinMarketCap: {str(e)}")

@app.get("/api/ltc-depth/{exchange}", response_model=DepthResponse, tags=["depth"])
async def get_ltc_depth(exchange: str):
    """
    Получает подробную информацию о глубине рынка для конкретной биржи.
    Пример для биржи Binance (для других бирж может потребоваться другая логика).
    
    - **exchange**: Название биржи (например, 'binance')
    """
    try:
        depth_data = None
        
        # Логика получения книги ордеров с разных бирж
        if exchange.lower() == 'binance':
            response = requests.get('https://api.binance.com/api/v3/depth', 
                                    params={'symbol': 'LTCUSDT', 'limit': 100})
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, 
                                    detail=f"Ошибка API Binance: {response.text}")
            
            depth_data = response.json()
        else:
            raise HTTPException(status_code=404, 
                                detail=f"Данные о глубине рынка для биржи {exchange} недоступны")
        
        # Получаем текущую цену LTC
        current_price = await get_current_ltc_price()
        plus_2_percent = current_price * 1.02
        minus_2_percent = current_price * 0.98
        
        # Расчет суммарного объема до +2% и -2% от текущей цены
        plus_2_percent_depth = 0
        minus_2_percent_depth = 0
        
        # Расчет для ордеров на покупку (bid)
        for price, volume in depth_data['bids']:
            if float(price) >= minus_2_percent:
                minus_2_percent_depth += float(price) * float(volume)
            else:
                break
        
        # Расчет для ордеров на продажу (ask)
        for price, volume in depth_data['asks']:
            if float(price) <= plus_2_percent:
                plus_2_percent_depth += float(price) * float(volume)
            else:
                break
        
        return {
            'status': 'success',
            'data': {
                'exchange': exchange,
                'currentPrice': current_price,
                'plus2PercentDepth': f"${math.floor(plus_2_percent_depth):,}",
                'minus2PercentDepth': f"${math.floor(minus_2_percent_depth):,}"
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, 
                            detail=f"Ошибка при получении данных о глубине рынка для {exchange}: {str(e)}")

async def get_current_ltc_price() -> float:
    """
    Вспомогательная функция для получения текущей цены LTC
    """
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price', 
                                params={'ids': 'litecoin', 'vs_currencies': 'usd'})
        if response.status_code != 200:
            return 0
        
        return response.json()['litecoin']['usd']
    except Exception as e:
        print(f"Ошибка при получении текущей цены LTC: {str(e)}")
        return 0

# Добавление эндпоинта для графика цены LTC

class PriceHistoryItem(BaseModel):
    """Модель для элемента истории цены"""
    date: str       # Дата в формате "месяц/день" (например, "3/23")
    price: float    # Цена в USD

class PriceHistoryResponse(BaseModel):
    """Модель для ответа с историей цены"""
    status: str
    data: List[PriceHistoryItem]
    currency: str = "USD"
    period: str

@app.get("/api/ltc-price-history", tags=["prices"])
async def get_ltc_price_history(days: int = 30, daily_close: bool = True):
    """
    Получает историю цены Litecoin за указанный период для построения графика.
    
    - **days**: Количество дней истории (по умолчанию 30 дней)
    - **daily_close**: Если True, возвращает только цены закрытия дня
    """
    try:
        # Ограничиваем maximum до 90 дней
        if days > 90:
            days = 90
        elif days < 1:
            days = 1
            
        # Проверяем наличие данных в кэше Redis с учетом параметра daily_close
        cache_key = f"ltc_price_history_new_format:{days}:{daily_close}"
        cached_data = redis_client.get(cache_key)
        
        if cached_data:
            # Если данные найдены в кэше, возвращаем их
            print(f"Возвращаем данные истории цен из кэша Redis за {days} дней")
            return json.loads(cached_data)
        
        # Если данных в кэше нет, получаем их из API CoinGecko
        print(f"Получаем данные истории цен из API CoinGecko за {days} дней")
        
        # Убираем параметр interval, так как API автоматически определит нужный интервал
        params = {
            'vs_currency': 'usd',
            'days': days
        }
        
        response = requests.get(
            'https://api.coingecko.com/api/v3/coins/litecoin/market_chart',
            params=params
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, 
                                detail=f"Ошибка API CoinGecko: {response.text}")
        
        data = response.json()
        prices = data.get('prices', [])  # Исторические цены в формате [timestamp, price]
        
        # Если нужны только цены закрытия дня
        if daily_close:
            # Группируем данные по дням и берем последнее значение для каждого дня
            daily_prices = {}
            for item in prices:
                timestamp, price = item
                # Преобразуем timestamp в дату без времени
                date_obj = datetime.fromtimestamp(timestamp / 1000)
                date_key = f"{date_obj.year}-{date_obj.month}-{date_obj.day}"
                
                # Сохраняем или обновляем цену для этого дня
                # Последняя запись для каждого дня будет перезаписывать предыдущие
                daily_prices[date_key] = {
                    'date': f"{date_obj.month}/{date_obj.day}",
                    'price': round(price, 2)
                }
            
            # Преобразуем словарь в список, сортируя по дате
            price_history = [daily_prices[key] for key in sorted(daily_prices.keys())]
        else:
            # Преобразуем данные в прежний формат с почасовой детализацией
            price_history = []
            for item in prices:
                timestamp, price = item
                date_obj = datetime.fromtimestamp(timestamp / 1000)
                formatted_date = f"{date_obj.month}/{date_obj.day}"
                
                price_history.append({
                    'date': formatted_date,
                    'price': round(price, 2)
                })
        
        # Определяем период
        if days <= 1:
            period = "24 часа"
        elif days <= 7:
            period = "7 дней"
        elif days <= 30:
            period = "1 месяц"
        else:
            period = f"{days} дней"
        
        result = {
            'status': 'success',
            'data': price_history,
            'currency': 'USD',
            'period': period
        }
        
        # Устанавливаем время кэширования в зависимости от запрошенного периода
        if days >= 30:
            ttl = 43200  # 12 часов в секундах
        elif days >= 7:
            ttl = 21600  # 6 часов в секундах
        else:
            ttl = 3600   # 1 час в секундах
            
        # Сохраняем результат в Redis с новым TTL
        redis_client.setex(cache_key, ttl, json.dumps(result, default=lambda o: o.__dict__))
        
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при получении истории цен LTC: {str(e)}")

# Функция для получения текущей цены LTC с Binance
async def get_binance_ltc_price() -> float:
    """Получение текущей цены LTC с Binance"""
    try:
        response = requests.get('https://api.binance.com/api/v3/ticker/price', params={'symbol': 'LTCUSDT'})
        if response.status_code == 200:
            data = response.json()
            return float(data['price'])
        else:
            return 0
    except Exception as e:
        print(f"Ошибка при получении цены LTC с Binance: {str(e)}")
        return 0

# Корневой маршрут с информацией об API
@app.get("/", tags=["info"])
async def root():
    """
    Возвращает общую информацию об API
    """
    return {
        "name": "LTC Exchange API",
        "version": "1.0.0",
        "description": "API для получения данных о биржах, торгующих Litecoin (LTC)",
        "endpoints": [
            {
                "path": "/api/ltc-exchanges",
                "description": "Получить данные о биржах LTC/USDT через CoinGecko"
            },
            {
                "path": "/api/ltc-exchanges-cmc",
                "description": "Получить данные о биржах LTC/USDT через CoinMarketCap"
            },
            {
                "path": "/api/ltc-depth/{exchange}",
                "description": "Получить данные о глубине рынка для конкретной биржи"
            },
            {
                "path": "/api/ltc-price-history",
                "description": "Получить историю цены Litecoin за указанный период для построения графика"
            }
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
