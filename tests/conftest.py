import pytest
from retailpulse.generate import Generator, Config

@pytest.fixture(scope='session')
def dataset():
    return Generator(Config(orders=1000,styles=2,customers=100)).run()
