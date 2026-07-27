"""helper classes"""
import re
import operator
import functools
import logging
from typing import Union
from contextlib import AbstractContextManager

logger = logging.getLogger(__name__)


def elaborate_setting_error(f):
    """wrapper method for elaborating the KeyError raising when attempting to retrieve a spectrometer setting that is
    is not defined"""
    @functools.wraps(f)
    def attempt_setting_retrieval(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except KeyError as e:
            raise KeyError(f'this spectrometer does not appear to have a "{e.args[0]}" setting')
    return attempt_setting_retrieval


class NMRVersion:
    _version_re = re.compile(
        '^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<sub>\d(\.\d+)*)'
    )

    def __init__(self,
                 version_string: str
                 ):
        match = self._version_re.search(version_string)
        if not match:
            raise ValueError(f'The version string "{version_string}" could not be interpreted')
        self.major = match.group('major')
        self.minor = match.group('minor')
        self.sub = match.group('sub')

    def __str__(self):
        return f'{self.major}.{self.minor}.{self.sub}'

    def __repr__(self):
        return f'{self.__class__.__name__}({self.__str__()})'

    @property
    def value(self) -> int:
        return int(f'{self.major}{self.minor}{self.sub.replace(".", "")}')

    def _compare(self, other: Union[str, float, int, "NMRVersion"], method):
        """
        Performs a comparison of the other and self using the provided method

        :param other: other value
        :param method: method to use for comparison
        :return:
        """
        if type(other) is int:
            return method(self.value, other)
        elif type(other) is float:
            return method(
                float(f'{self.major}.{self.minor}'),
                other
            )
        elif type(other) is str:
            return method(
                self.value,
                NMRVersion(other).value,
            )
        elif isinstance(other, self.__class__):
            return self._compare(
                other.value,
                method
            )
        else:
            raise TypeError(f'The provided value {other} is an unrecognized type: {type(other)}')

    def __lt__(self, other: Union[str, float, int]):
        return self._compare(
            other,
            operator.lt
        )

    def __le__(self, other):
        return self._compare(
            other,
            operator.le
        )

    def __eq__(self, other):
        return self._compare(
            other,
            operator.eq
        )

    def __ge__(self, other):
        return self._compare(
            other,
            operator.ge
        )

    def __gt__(self, other):
        return self._compare(
            other,
            operator.gt
        )


class TemporarilySet(AbstractContextManager):
    def __init__(self,
                 instance,
                 attr: str,
                 val,
                 ):
        """
        Temporarily sets the attribute of the instance then reverts it on completion. The instance must support
        get and set of the specified attribute.

        :param instance: instance to modify
        :param attr: attribute to set
        :param val: value to set temporarily
        """
        if hasattr(instance, attr) is False:
            raise AttributeError(f'the {instance.__class__.__name__} instance does not have a "{attr}" attribute')
        self.instance = instance
        self.attr = attr
        self.temporary = val
        # retrieve current value and store
        self.original = getattr(self.instance, self.attr)

    def __enter__(self) -> 'TemporarilySet':
        logger.debug(f'{self.instance} {self.attr} original value: {self.original}')
        # if different than previous value, set temporary attribute
        if self.current != self.temporary:
            logger.info(f'setting {self.attr} to {self.temporary}')
            setattr(self.instance, self.attr, self.temporary)
        else:
            logger.info(f'{self.instance} {self.attr} value is already set to {self.temporary}')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit()

    @property
    def current(self):
        """current value of the attribute"""
        return getattr(self.instance, self.attr)

    def enter(self):
        """enters the instance outside of a with statement"""
        self.__enter__()

    def exit(self):
        """exits the instance outside of a with statement"""
        # if value was changed, change back
        if self.current != self.original:
            logger.info(f'restoring {self.attr} to {self.original}')
            setattr(self.instance, self.attr, self.original)
