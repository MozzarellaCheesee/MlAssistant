import numpy as np

MAX_TOKENS = 128


class E5Embedder:
    REPO = "intfloat/multilingual-e5-small"

    def __init__(self, session, tokenizer):
        self.session = session
        self.tok = tokenizer
        self.names = [inp.name for inp in session.get_inputs()]

    @classmethod
    def load(cls) -> "E5Embedder":
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(hf_hub_download(cls.REPO, "onnx/tokenizer.json"))
        tok.enable_padding()
        tok.enable_truncation(MAX_TOKENS)
        return cls(
            ort.InferenceSession(hf_hub_download(cls.REPO, "onnx/model.onnx"), providers=["CPUExecutionProvider"]), tok
        )

    def _emb(self, texts: list[str]) -> list[list[float]]:
        encoded = self.tok.encode_batch(texts)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feed)[0]
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return (pooled / np.linalg.norm(pooled, axis=1, keepdims=True)).tolist()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._emb(["passage: " + t for t in texts]) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._emb(["query: " + text])[0]
