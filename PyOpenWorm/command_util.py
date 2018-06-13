class IVar(object):
    '''
    A descriptor for instance variables amended to provide some attributes like default values, value types, etc.
    '''
    def __init__(self, default_value=None, doc=None, value_type=str, name=None):
        self.default_value = default_value
        self.name = '_ivar_' + str(id(self)) if name is None else name
        self.__doc__ = doc.strip() if doc is not None else doc
        self.value_type = value_type

    def __get__(self, target, typ=None):
        return getattr(target, self.name, self.default_value)

    def __set__(self, target, value):
        setattr(target, self.name, value)

    @classmethod
    def property(cls, wrapped=None, *args, **kwargs):
        print(wrapped, args, kwargs)
        if wrapped is not None and \
                callable(wrapped) and \
                len(args) == 0 and \
                len(kwargs) == 0: # we only got the wrapped. Just execute now.
            res = PropertyIVar()
            res.value_getter = wrapped
            res.__doc__ = getattr(wrapped, '__doc__', '').strip()
            return res

        first_arg = wrapped
        aargs = list(args)

        def wrapper(wrapped):
            if first_arg is not None:
                args = [first_arg] + list(aargs)
            else:
                args = aargs
            res = PropertyIVar(*args, **kwargs)
            res.value_getter = wrapped
            res.__doc__ = wrapped.__doc__
            return res
        return wrapper


class PropertyIVar(IVar):
    def __init__(self, *args, **kwargs):
        super(PropertyIVar, self).__init__(*args, **kwargs)
        self._have_set = True
        self.value_getter = None
        self.value_setter = None

    def setter(self, fset):
        res = type(self)(default_value=self.default_value,
                         doc=self.__doc__,
                         value_type=self.value_type,
                         name=self.name)
        res.value_getter = self.value_getter
        res.value_setter = fset
        res._have_set = False
        return res

    def __set__(self, target, value):
        if self.value_setter is None:
            raise AttributeError("can't set attribute")
        self.value_setter(target, value)
        self._have_set = True

    def __get__(self, target, objectype=None):
        ''' Executes the provided getter

        When the getter is first called, and when a setter is also defined, the setter will be called with the default
        value before the getter is called for the first time. _Even if the default_value is not set explicitly, the
        setter will still be called with 'None'._
        '''
        if not self._have_set:
            self.value_setter(target, self.default_value)
        return self.value_getter(target)
