from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CredentialCipher:
    def __init__(self, keys: str) -> None:
        self._cipher = MultiFernet([Fernet(key.encode("ascii")) for key in keys.split(",")])

    def encrypt(self, plaintext: str) -> str:
        return self._cipher.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, envelope: str) -> str:
        try:
            return self._cipher.decrypt(envelope.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as error:
            raise ValueError("Credential envelope cannot be decrypted") from error

    def rotate(self, envelope: str) -> str:
        try:
            return self._cipher.rotate(envelope.encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeError, ValueError) as error:
            raise ValueError("Credential envelope cannot be rotated") from error
