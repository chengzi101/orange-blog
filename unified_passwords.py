"""New hashes are Argon2id; legacy hashes are verified without rewriting them."""
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from werkzeug.security import check_password_hash as legacy_check
import unicodedata

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

def validate_password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 128 or value.isspace():
        raise ValueError('密码需为10–128个字符。')
    groups = [any(c.isalpha() for c in value), any(c.isdigit() for c in value),
              any(not c.isalnum() and not c.isspace() for c in value)]
    if sum(groups) < 2:
        raise ValueError('密码至少包含字母、数字、符号中的两类。')
    return value

def validate_username(value):
    value = unicodedata.normalize('NFKC', value).strip()
    if not 1 <= len(value) <= 24 or any(not (c.isalnum() or c in '_-') for c in value):
        raise ValueError('用户名需为1–24个汉字、字母、数字、下划线或短横线。')
    return value

def generate_password_hash(value, **ignored):
    return hasher.hash(value)

def check_password_hash(encoded, value):
    if not encoded or not isinstance(value, str) or len(value) > 128:
        return False
    try:
        return hasher.verify(encoded, value) if encoded.startswith('$argon2') else legacy_check(encoded, value)
    except (VerificationError, InvalidHashError, ValueError, TypeError):
        return False
