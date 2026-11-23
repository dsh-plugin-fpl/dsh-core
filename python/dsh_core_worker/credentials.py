"""Encrypted provider credentials committed with their catalog configuration."""

from cryptography.fernet import InvalidToken

from .contracts import CoreError


class ProviderCredentials:
    def __init__(self, store, cipher):
        self.store, self.cipher = store, cipher

    def secret(self, reference):
        ciphertext = self.store.config("credential:" + reference, "")["value"]
        if not ciphertext:
            return ""
        try:
            return self.cipher.decrypt(ciphertext.encode()).decode()
        except InvalidToken as error:
            raise CoreError(
                "模型凭据无法解密，请检查此服务的原始密钥", "credential_unreadable"
            ) from error

    def encrypted(self, value):
        if not isinstance(value, str) or len(value) > 16384:
            raise CoreError("API Key 长度无效")
        return self.cipher.encrypt(value.encode()).decode()
