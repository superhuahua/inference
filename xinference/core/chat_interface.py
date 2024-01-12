# Copyright 2022-2023 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import logging
import os
from io import BytesIO
from typing import Generator, List, Optional

import gradio as gr
import PIL.Image
from gradio.components import Markdown, Textbox
from gradio.layouts import Accordion, Column, Row

from ..client.restful.restful_client import (
    RESTfulChatglmCppChatModelHandle,
    RESTfulChatModelHandle,
    RESTfulGenerateModelHandle,
    RESTfulMultimodalModelHandle,
)
from ..types import ChatCompletionMessage

logger = logging.getLogger(__name__)


class GradioInterface:
    def __init__(
        self,
        endpoint: str,
        model_uid: str,
        model_name: str,
        model_size_in_billions: int,
        model_type: str,
        model_format: str,
        quantization: str,
        context_length: int,
        model_ability: List[str],
        model_description: str,
        model_lang: List[str],
        access_token: Optional[str],
    ):
        self.endpoint = endpoint
        self.model_uid = model_uid
        self.model_name = model_name
        self.model_size_in_billions = model_size_in_billions
        self.model_type = model_type
        self.model_format = model_format
        self.quantization = quantization
        self.context_length = context_length
        self.model_ability = model_ability
        self.model_description = model_description
        self.model_lang = model_lang
        self._access_token = (
            access_token.replace("Bearer ", "") if access_token is not None else None
        )

    def build(self) -> "gr.Blocks":
        if self.model_type == "multimodal":
            interface = self.build_chat_vl_interface()
        elif "chat" in self.model_ability:
            interface = self.build_chat_interface()
        else:
            interface = self.build_generate_interface()

        interface.queue()
        # Gradio initiates the queue during a startup event, but since the app has already been
        # started, that event will not run, so manually invoke the startup events.
        # See: https://github.com/gradio-app/gradio/issues/5228
        interface.startup_events()
        favicon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.path.pardir,
            "web",
            "ui",
            "public",
            "favicon.svg",
        )
        interface.favicon_path = favicon_path
        return interface

    def build_chat_interface(
        self,
    ) -> "gr.Blocks":
        def flatten(matrix: List[List[str]]) -> List[str]:
            flat_list = []
            for row in matrix:
                flat_list += row
            return flat_list

        def to_chat(lst: List[str]) -> List[ChatCompletionMessage]:
            res = []
            for i in range(len(lst)):
                role = "assistant" if i % 2 == 1 else "user"
                res.append(ChatCompletionMessage(role=role, content=lst[i]))
            return res

        def generate_wrapper(
            message: str,
            history: List[List[str]],
            max_tokens: int,
            temperature: float,
        ) -> Generator:
            from ..client import RESTfulClient

            client = RESTfulClient(self.endpoint)
            client._set_token(self._access_token)
            model = client.get_model(self.model_uid)
            assert isinstance(
                model, (RESTfulChatModelHandle, RESTfulChatglmCppChatModelHandle)
            )

            response_content = ""
            for chunk in model.chat(
                prompt=message,
                chat_history=to_chat(flatten(history)),
                generate_config={
                    "max_tokens": int(max_tokens),
                    "temperature": temperature,
                    "stream": True,
                },
            ):
                assert isinstance(chunk, dict)
                delta = chunk["choices"][0]["delta"]
                if "content" not in delta:
                    continue
                else:
                    response_content += delta["content"]
                    yield response_content

            yield response_content

        return gr.ChatInterface(
            fn=generate_wrapper,
            additional_inputs=[
                gr.Slider(
                    minimum=1,
                    maximum=self.context_length,
                    value=512,
                    step=1,
                    label="Max Tokens",
                ),
                gr.Slider(
                    minimum=0, maximum=2, value=1, step=0.01, label="Temperature"
                ),
            ],
            title=f"🚀 Xinference Chat Bot : {self.model_name} 🚀",
            css="""
            .center{
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 0px;
                color: #9ea4b0 !important;
            }
            """,
            description=f"""
            <div class="center">
            Model ID: {self.model_uid}
            </div>
            <div class="center">
            Model Size: {self.model_size_in_billions} Billion Parameters
            </div>
            <div class="center">
            Model Format: {self.model_format}
            </div>
            <div class="center">
            Model Quantization: {self.quantization}
            </div>
            """,
            analytics_enabled=False,
        )

    def build_chat_vl_interface(
        self,
    ) -> "gr.Blocks":
        def predict(history, bot):
            logger.debug("Predict model: %s, history: %s", self.model_uid, history)
            from ..client import RESTfulClient

            client = RESTfulClient(self.endpoint)
            client._set_token(self._access_token)
            model = client.get_model(self.model_uid)
            assert isinstance(model, RESTfulMultimodalModelHandle)

            prompt = history[-1]
            assert prompt["role"] == "user"
            prompt = prompt["content"]
            # multimodal chat does not support stream.
            response = model.chat(prompt=prompt, chat_history=history[:-1])
            history.append(response["choices"][0]["message"])
            bot[-1][1] = history[-1]["content"]
            return history, bot

        def add_text(history, bot, text, image):
            logger.debug("Add text, text: %s, image: %s", text, image)
            if image:
                buffered = BytesIO()
                with PIL.Image.open(image) as img:
                    img.thumbnail((500, 500))
                    img.save(buffered, format="JPEG")
                img_b64_str = base64.b64encode(buffered.getvalue()).decode()
                display_content = f'<img src="data:image/png;base64,{img_b64_str}" alt="user upload image" />\n{text}'
                message = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                }
            else:
                display_content = text
                message = {"role": "user", "content": text}
            history = history + [message]
            bot = bot + [(display_content, None)]
            return history, bot, "", None

        def clear_history():
            logger.debug("Clear history.")
            return [], None, "", None

        def update_button(text):
            return gr.update(interactive=bool(text))

        with gr.Blocks(
            title=f"🚀 Xinference Chat Bot : {self.model_name} 🚀",
            css="""
        .center{
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 0px;
            color: #9ea4b0 !important;
        }
        """,
            analytics_enabled=False,
        ) as chat_vl_interface:
            Markdown(
                f"""
                <h1 style='text-align: center; margin-bottom: 1rem'>🚀 Xinference Chat Bot : {self.model_name} 🚀</h1>
                """
            )
            Markdown(
                f"""
                <div class="center">
                Model ID: {self.model_uid}
                </div>
                <div class="center">
                Model Size: {self.model_size_in_billions} Billion Parameters
                </div>
                <div class="center">
                Model Format: {self.model_format}
                </div>
                <div class="center">
                Model Quantization: {self.quantization}
                </div>
                """
            )

            state = gr.State([])
            with gr.Row():
                chatbot = gr.Chatbot(
                    elem_id="chatbot", label=self.model_name, height=550, scale=7
                )
                with gr.Column(scale=3):
                    imagebox = gr.Image(type="filepath")
                    textbox = gr.Textbox(
                        show_label=False,
                        placeholder="Enter text and press ENTER",
                        container=False,
                    )
                    submit_btn = gr.Button(
                        value="Send", variant="primary", interactive=False
                    )
                    clear_btn = gr.Button(value="Clear")

            textbox.change(update_button, [textbox], [submit_btn], queue=False)

            textbox.submit(
                add_text,
                [state, chatbot, textbox, imagebox],
                [state, chatbot, textbox, imagebox],
                queue=False,
            ).then(predict, [state, chatbot], [state, chatbot])

            submit_btn.click(
                add_text,
                [state, chatbot, textbox, imagebox],
                [state, chatbot, textbox, imagebox],
                queue=False,
            ).then(predict, [state, chatbot], [state, chatbot])

            clear_btn.click(
                clear_history, None, [state, chatbot, textbox, imagebox], queue=False
            )

        return chat_vl_interface

    def build_generate_interface(
        self,
    ):
        def undo(text, hist):
            if len(hist) == 0:
                return {
                    textbox: "",
                    history: [text],
                }
            if text == hist[-1]:
                hist = hist[:-1]

            return {
                textbox: hist[-1] if len(hist) > 0 else "",
                history: hist,
            }

        def clear(text, hist):
            if len(hist) == 0 or (len(hist) > 0 and text != hist[-1]):
                hist.append(text)
            hist.append("")
            return {
                textbox: "",
                history: hist,
            }

        def complete(text, hist, max_tokens, temperature) -> Generator:
            from ..client import RESTfulClient

            client = RESTfulClient(self.endpoint)
            client._set_token(self._access_token)
            model = client.get_model(self.model_uid)
            assert isinstance(model, RESTfulGenerateModelHandle)

            if len(hist) == 0 or (len(hist) > 0 and text != hist[-1]):
                hist.append(text)

            response_content = text
            for chunk in model.generate(
                prompt=text,
                generate_config={
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
            ):
                assert isinstance(chunk, dict)
                choice = chunk["choices"][0]
                if "text" not in choice:
                    continue
                else:
                    response_content += choice["text"]
                    yield {
                        textbox: response_content,
                        history: hist,
                    }

            hist.append(response_content)
            return {
                textbox: response_content,
                history: hist,
            }

        def retry(text, hist, max_tokens, temperature) -> Generator:
            from ..client import RESTfulClient

            client = RESTfulClient(self.endpoint)
            client._set_token(self._access_token)
            model = client.get_model(self.model_uid)
            assert isinstance(model, RESTfulGenerateModelHandle)

            if len(hist) == 0 or (len(hist) > 0 and text != hist[-1]):
                hist.append(text)
            text = hist[-2] if len(hist) > 1 else ""

            response_content = text
            for chunk in model.generate(
                prompt=text,
                generate_config={
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
            ):
                assert isinstance(chunk, dict)
                choice = chunk["choices"][0]
                if "text" not in choice:
                    continue
                else:
                    response_content += choice["text"]
                    yield {
                        textbox: response_content,
                        history: hist,
                    }

            hist.append(response_content)
            return {
                textbox: response_content,
                history: hist,
            }

        with gr.Blocks(
            title=f"🚀 Xinference Generate Bot : {self.model_name} 🚀",
            css="""
            .center{
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 0px;
                color: #9ea4b0 !important;
            }
            """,
            analytics_enabled=False,
        ) as generate_interface:
            history = gr.State([])

            Markdown(
                f"""
                <h1 style='text-align: center; margin-bottom: 1rem'>🚀 Xinference Generate Bot : {self.model_name} 🚀</h1>
                """
            )
            Markdown(
                f"""
                <div class="center">
                Model ID: {self.model_uid}
                </div>
                <div class="center">
                Model Size: {self.model_size_in_billions} Billion Parameters
                </div>
                <div class="center">
                Model Format: {self.model_format}
                </div>
                <div class="center">
                Model Quantization: {self.quantization}
                </div>
                """
            )

            with Column(variant="panel"):
                textbox = Textbox(
                    container=False,
                    show_label=False,
                    label="Message",
                    placeholder="Type a message...",
                    lines=21,
                    max_lines=50,
                )

                with Row():
                    btn_generate = gr.Button("Generate", variant="primary")
                with Row():
                    btn_undo = gr.Button("↩️  Undo")
                    btn_retry = gr.Button("🔄  Retry")
                    btn_clear = gr.Button("🗑️  Clear")
                with Accordion("Additional Inputs", open=False):
                    length = gr.Slider(
                        minimum=1,
                        maximum=self.context_length,
                        value=1024,
                        step=1,
                        label="Max Tokens",
                    )
                    temperature = gr.Slider(
                        minimum=0, maximum=2, value=1, step=0.01, label="Temperature"
                    )

                btn_generate.click(
                    fn=complete,
                    inputs=[textbox, history, length, temperature],
                    outputs=[textbox, history],
                )

                btn_undo.click(
                    fn=undo,
                    inputs=[textbox, history],
                    outputs=[textbox, history],
                )

                btn_retry.click(
                    fn=retry,
                    inputs=[textbox, history, length, temperature],
                    outputs=[textbox, history],
                )

                btn_clear.click(
                    fn=clear,
                    inputs=[textbox, history],
                    outputs=[textbox, history],
                )

        return generate_interface
