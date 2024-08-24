# Copyright 2023 LiveKit, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncContextManager, AsyncIterator
from urllib.parse import urlencode

import aiohttp
from livekit.agents import tts, utils

from .log import logger
from .models import TTSModels

DEEPGRAM_TTS_SAMPLE_RATE = 22050
DEEPGRAM_TTS_CHANNELS = 1
BASE_URL = "https://api.deepgram.com/v1/speak"


@dataclass
class _TTSOptions:
    model: TTSModels


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        model: TTSModels = "aura-asteria-en",
        api_key: str | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(
                streaming=False,
            ),
            sample_rate=DEEPGRAM_TTS_SAMPLE_RATE,
            num_channels=DEEPGRAM_TTS_CHANNELS,
        )

        self._api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        if self._api_key is None:
            raise ValueError("Deepgram API key is required")

        self._opts = _TTSOptions(
            model=model,
        )
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()

        return self._session

    def synthesize(self, text: str) -> "ChunkedStream":
        return ChunkedStream(self._api_key, text, self._opts, self._ensure_session())

    def stream(self) -> AsyncContextManager[tts.TTSStream]:
        raise NotImplementedError("Streaming is not supported by this TTS implementation")


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        api_key: str,
        text: str,
        opts: _TTSOptions,
        http_session: aiohttp.ClientSession,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._text = text
        self._opts = opts
        self._session = http_session

    @utils.log_exceptions(logger=logger)
    async def _main_task(self):
        request_id = utils.shortuuid()
        segment_id = utils.shortuuid()

        config = {
            "model": self._opts.model,
            "encoding": "mp3"
        }

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{BASE_URL}?{urlencode(config)}"
        logger.info(f"Requesting TTS for text: {self._text[:50]}...")  # Log only the first 50 characters

        try:
            async with self._session.post(url, headers=headers, json={"text": self._text}) as response:
                response.raise_for_status()
                decoder = utils.codecs.Mp3StreamDecoder()
                async for chunk in response.content.iter_any():
                    for frame in decoder.decode_chunk(chunk):
                        self._event_ch.send_nowait(
                            tts.SynthesizedAudio(
                                request_id=request_id,
                                segment_id=segment_id,
                                frame=frame
                            )
                        )

        except Exception as e:
            logger.error(f"Deepgram TTS API error: {e}")
        finally:
            self._event_ch.close()