from __future__ import print_function
import importlib as IM
import logging
from itertools import count

import rdflib as R

from .utils import FCN
from .configure import Configurable
from .module_recorder import ModuleRecordListener


__all__ = ["Mapper",
           "UnmappedClassException"]

L = logging.getLogger(__name__)


class UnmappedClassException(Exception):
    pass


class ClassRedefinitionAttempt(Exception):
    '''
    Thrown when a `.Mapper.add_class` is called on a class when a class with the same name
    has already been added to the mapper
    '''
    def __init__(self, mapper, maybe_cls, cls):
        super(ClassRedefinitionAttempt, self).__init__(
                'Attempted to add class %s to %s when %s had already been added' % (
                    maybe_cls, mapper, cls))


class Mapper(ModuleRecordListener, Configurable):
    '''
    Keeps track of relationships between classes, between modules, and between classes and modules
    '''
    def __init__(self, base_namespace=None, imported=(), name=None, **kwargs):
        super(Mapper, self).__init__(**kwargs)

        """ Maps class names to classes """
        self.MappedClasses = dict()

        """ Maps classes to decorated versions of the class """
        self.DecoratedMappedClasses = dict()

        """ Maps RDF types to properties of the related class """
        self.RDFTypeTable = dict()

        if base_namespace is None:
            base_namespace = R.Namespace("http://example.com#")
        elif not isinstance(base_namespace, R.Namespace):
            base_namespace = R.Namespace(base_namespace)

        """ Base namespace used if a mapped class doesn't define its own """
        self.base_namespace = base_namespace

        """ Modules that have already been loaded """
        self.modules = dict()

        self.imported_mappers = imported

        if name is None:
            name = hex(id(self))
        self.name = name

    def decorate_class(self, cls):
        '''
        Extension point for subclasses of Mapper to apply an operation to all mapped classes
        '''
        return cls

    def add_class(self, cls):
        cname = FCN(cls)
        maybe_cls = self._lookup_class(cname)
        if maybe_cls is not None:
            if maybe_cls is cls:
                return False
            else:
                raise ClassRedefinitionAttempt(self, maybe_cls, cls)
        L.debug("Adding class %s@0x%x", cls, id(cls))

        self.MappedClasses[cname] = cls
        self.DecoratedMappedClasses[cls] = self.decorate_class(cls)
        parents = cls.__bases__
        L.debug('parents %s', parents_str(cls))

        if hasattr(cls, 'on_mapper_add_class'):
            cls.on_mapper_add_class(self)

        # This part happens after the on_mapper_add_class has run since the
        # class has an opportunity to set its RDF type based on what we provide
        # in the Mapper.
        self.RDFTypeTable[cls.rdf_type] = cls
        return True

    def load_module(self, module_name):
        """ Loads the module. """
        module = self.lookup_module(module_name)
        if not module:
            module = IM.import_module(module_name)
            return self.process_module(module_name, module)
        else:
            return module

    def process_module(self, module_name, module):
        self.modules[module_name] = module
        for c in self._module_load_helper(module):
            try:
                if hasattr(c, 'after_mapper_module_load'):
                    c.after_mapper_module_load(self)
            except Exception:
                L.warning("Failed to process class", c)
                continue
        return module

    def process_class(self, *classes):
        for c in classes:
            self.add_class(c)
            if hasattr(c, 'after_mapper_module_load'):
                c.after_mapper_module_load(self)

    process_classes = process_class

    def lookup_module(self, module_name):
        m = self.modules.get(module_name, None)
        if m is None:
            for p in self.imported_mappers:
                m = p.lookup_module(module_name)
                if m:
                    break
        return m

    def load_class(self, cname_or_mname, cnames=None):
        if cnames:
            mpart = cname_or_mname
        else:
            mpart, cpart = cname_or_mname.rsplit('.', 1)
            cnames = (cpart,)
        m = self.load_module(mpart)
        try:
            res = tuple(self.DecoratedMappedClasses[c]
                        if c in self.DecoratedMappedClasses
                        else c
                        for c in
                        (getattr(m, cname) for cname in cnames))

            return res[0] if len(res) == 1 else res
        except AttributeError:
            raise UnmappedClassException(cnames)

    def _module_load_helper(self, module):
        # TODO: Make this class selector pluggable
        return self.handle_mapped_classes(getattr(module, '__yarom_mapped_classes__', ()))

    def handle_mapped_classes(self, classes):
        res = []
        for cls in classes:
            # This previously used the
            full_class_name = FCN(cls)
            if isinstance(cls, type) and self.add_class(cls):
                res.append(cls)
        return res # sorted(res, key=_ClassOrderable, reverse=True)

    def lookup_class(self, cname):
        """ Gets the class corresponding to a fully-qualified class name """
        ret = self._lookup_class(cname)
        if ret is None:
            raise UnmappedClassException((cname,))
        return ret

    def _lookup_class(self, cname):
        c = self.MappedClasses.get(cname, None)
        if c is None:
            for p in self.imported_mappers:
                c = p._lookup_class(cname)
                if c:
                    break
        else:
            L.debug('%s.lookup_class("%s") %s@%s',
                    repr(self), cname, c, hex(id(c)))
        return c

    def mapped_classes(self):
        for p in self.imported_mappers:
            for c in p.mapped_classes():
                yield
        for c in self.MappedClasses.values():
            yield c

    def __str__(self):
        if self.name is not None:
            return 'Mapper(name="'+str(self.name)+'")'
        else:
            return super(Mapper, self).__str__()


class _ClassOrderable(object):
    def __init__(self, cls):
        self.cls = cls

    def __eq__(self, other):
        self.cls is other.cls

    def __gt__(self, other):
        res = False
        ocls = other.cls
        scls = self.cls
        if issubclass(ocls, scls) and not issubclass(scls, ocls):
            res = True
        elif issubclass(scls, ocls) == issubclass(ocls, scls):
            res = scls.__name__ > ocls.__name__
        return res

    def __lt__(self, other):
        res = False
        ocls = other.cls
        scls = self.cls
        if issubclass(scls, ocls) and not issubclass(ocls, scls):
            res = True
        elif issubclass(scls, ocls) == issubclass(ocls, scls):
            res = scls.__name__ < ocls.__name__
        return res


def mapped(cls):
    '''
    A decorator for declaring that a class is 'mapped'. This is required for Mapper to
    find the class
    '''
    module = IM.import_module(cls.__module__)
    if not hasattr(module, '__yarom_mapped_classes__'):
        module.__yarom_mapped_classes__ = [cls]
    else:
        module.__yarom_mapped_classes__.append(cls)

    return cls


def parents_str(cls):
    return ", ".join(p.__name__ + '@' + hex(id(p)) for p in cls.mro())
