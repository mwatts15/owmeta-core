from collections import namedtuple
from io import StringIO
from os.path import join as p
from os import makedirs, chmod
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock

import pytest
import rdflib
from rdflib.term import URIRef
from rdflib.graph import ConjunctiveGraph


from owmeta_core.context import Context
from owmeta_core.contextualize import Contextualizable
from owmeta_core.bundle import (Remote, URLConfig, HTTPBundleLoader, Bundle, BundleNotFound,
                                Descriptor, DependencyDescriptor, _RemoteHandlerMixin,
                                Installer, make_include_func, BUNDLE_INDEXED_DB_NAME)
from owmeta_core.agg_store import UnsupportedAggregateOperation


Dirs = namedtuple('Dirs', ('source_directory', 'bundles_directory'))


@pytest.fixture
def dirs():
    with TemporaryDirectory() as source_directory,\
            TemporaryDirectory() as bundles_directory:
        yield Dirs(source_directory, bundles_directory)


def test_write_read_remote_1():
    out = StringIO()
    r0 = Remote('remote')
    r0.write(out)
    out.seek(0)
    r1 = Remote.read(out)
    assert r0 == r1


def test_write_read_remote_2():
    out = StringIO()
    r0 = Remote('remote')
    r0.add_config(URLConfig('http://example.org/bundle_remote0'))
    r0.add_config(URLConfig('http://example.org/bundle_remote1'))
    r0.write(out)
    out.seek(0)
    r1 = Remote.read(out)
    assert r0 == r1


def test_get_http_url_loaders():
    '''
    Find loaders for HTTP URLs
    '''
    r0 = Remote('remote')
    r0.add_config(URLConfig('http://example.org/bundle_remote0'))
    for l in r0.generate_loaders():
        if isinstance(l, HTTPBundleLoader):
            return

    raise AssertionError('No HTTPBundleLoader was created')


def test_remote_generate_uploaders_skip():
    mock = Mock()
    with patch('owmeta_core.bundle.UPLOADER_CLASSES', [mock]):
        r0 = Remote('remote')
        r0.add_config(URLConfig('http://example.org/bundle_remote0'))
        for ul in r0.generate_uploaders():
            pass
    mock.can_upload_to.assert_called()


def test_remote_generate_uploaders_no_skip():
    mock = Mock()
    mock.can_upload_to.return_value = True
    ac = URLConfig('http://example.org/bundle_remote0')
    with patch('owmeta_core.bundle.UPLOADER_CLASSES', [mock]):
        r0 = Remote('remote')
        r0.add_config(ac)
        for ul in r0.generate_uploaders():
            pass
    mock.assert_called_with(ac)


def test_latest_bundle_fetched(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    makedirs(p(bundles_directory, 'example', '1'))
    makedirs(p(bundles_directory, 'example', '2'))
    expected = p(bundles_directory, 'example', '3')
    makedirs(expected)
    b = Bundle('example', bundles_directory=bundles_directory)
    assert expected == b._get_bundle_directory()


def test_specified_version_fetched(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    makedirs(p(bundles_directory, 'example', '1'))
    expected = p(bundles_directory, 'example', '2')
    makedirs(expected)
    makedirs(p(bundles_directory, 'example', '3'))
    b = Bundle('example', version=2, bundles_directory=bundles_directory)
    assert expected == b._get_bundle_directory()


def test_no_versioned_bundles(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    makedirs(p(bundles_directory, 'example'))
    b = Bundle('example', bundles_directory=bundles_directory)
    with pytest.raises(BundleNotFound, match='No versioned bundle directories'):
        b._get_bundle_directory()


def test_specified_bundle_does_not_exist(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    makedirs(p(bundles_directory, 'example'))
    b = Bundle('example', bundles_directory=bundles_directory, version=2)
    with pytest.raises(BundleNotFound, match='at version 2.*specified version'):
        b._get_bundle_directory()


def test_specified_bundle_directory_does_not_exist(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    makedirs(bundles_directory)
    b = Bundle('example', bundles_directory=bundles_directory)
    with pytest.raises(BundleNotFound, match='Bundle directory'):
        b._get_bundle_directory()


def test_specified_bundles_root_directory_does_not_exist(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    b = Bundle('example', bundles_directory=bundles_directory)
    with pytest.raises(BundleNotFound, match='Bundle directory'):
        b._get_bundle_directory()


def test_specified_bundles_root_permission_denied(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    b = Bundle('example', bundles_directory=bundles_directory)
    makedirs(bundles_directory)
    chmod(bundles_directory, 0)
    try:
        with pytest.raises(OSError, match='[Pp]ermission denied'):
            b._get_bundle_directory()
    finally:
        chmod(bundles_directory, 0o777)


def test_ignore_non_version_number(tempdir):
    bundles_directory = p(tempdir, 'bundles')
    b = Bundle('example', bundles_directory=bundles_directory)
    makedirs(p(bundles_directory, 'example', 'ignore_me'))
    expected = p(bundles_directory, 'example', '5')
    makedirs(expected)
    actual = b._get_bundle_directory()
    assert actual == expected


def test_descriptor_dependency():
    d = Descriptor.make({
        'id': 'testBundle',
        'dependencies': [
            'dep1',
            {'id': 'dep2', 'version': 2},
            ('dep3', 4),
            ('dep4',)
        ]
    })
    assert DependencyDescriptor('dep1') in d.dependencies
    assert DependencyDescriptor('dep2', 2) in d.dependencies
    assert DependencyDescriptor('dep3', 4) in d.dependencies
    assert DependencyDescriptor('dep4') in d.dependencies


def test_descriptor_includes_extra_key():
    with pytest.raises(ValueError, match=r'.*empty.*'):
        Descriptor.load('''
        id: dep
        includes:
            - http://example.org/empty_ctx:
              empty: true # Oh no, you don't belong here...
        ''')


def test_descriptor_empties():
    d = Descriptor.load('''
    id: dep
    includes:
        - http://example.org/empty_ctx:
            empty: true
    ''')
    assert 'http://example.org/empty_ctx' in d.empties


def test_descriptor_includes_empty_false():
    d = Descriptor.load('''
    id: dep
    includes:
        - http://example.org/empty_ctx:
            empty: false
    ''')
    assert not d.empties


def test_triple_in_dependency(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    trip = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'))
    ctx_graph.add(trip)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert trip in bnd.rdf


def test_quad_in_dependency(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert quad in bnd.rdf


def test_quad_not_in_dependency(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/other_ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert quad not in bnd.rdf


def test_triples_choices(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')])):
            match = True
            break
        assert match


def test_triples_choices_context(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')]),
                context=ctx_graph):
            match = True
            break
        assert match


def test_triples_choices_context_not_included(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctxg
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')]),
                context=ctx_graph):
            match = True
        assert not match


def test_add_to_graph_not_supported(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        with pytest.raises(UnsupportedAggregateOperation):
            bnd.rdf.add(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 URIRef('http://example.org/obj')))


def test_remote_handler_mixin_configured_remotes():
    class A(_RemoteHandlerMixin):
        def __init__(self):
            m = Mock()
            m.name = 'a_remote'
            self.remotes = [m]

    cut = A()
    remote = None
    for r in cut._get_remotes(()):
        remote = r
    assert remote is not None


def test_remote_handler_mixin_selected_configured_remotes():
    class A(_RemoteHandlerMixin):
        def __init__(self):
            m = Mock()
            m.name = 'a_remote'
            self.remotes = [m]

    cut = A()
    remote = None
    for r in cut._get_remotes(('a_remote',)):
        remote = r
    assert remote is not None


def test_bundle_contextualize_non_contextualizable(tempdir, bundle):
    cut = Bundle(bundle.descriptor.id, version=bundle.descriptor.version,
            bundles_directory=bundle.bundles_directory)
    token = object()
    assert cut(token) == token


def test_bundle_contextualize(tempdir, bundle):
    with Bundle(bundle.descriptor.id, version=bundle.descriptor.version,
            bundles_directory=bundle.bundles_directory) as cut:
        ctxble = Mock(spec=Contextualizable)
        cut(ctxble)
        ctxble.contextualize.assert_called_with(ContextWithNoId())


class ContextWithNoId(Context):
    def __eq__(self, other):
        return isinstance(other, Context) and other.identifier is None


def test_transitive_dep_null_context_triples_no_imports(custom_bundle):
    dep_dep_desc = Descriptor.load('''
    id: dep_dep
    includes:
      - http://example.com/ctx
    ''')

    dep_desc = Descriptor.load('''
    id: dep
    dependencies:
      - dep_dep
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_dep_desc, graph=depgraph) as depdepbun, \
            custom_bundle(dep_desc, bundles_directory=depdepbun.bundles_directory) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert set([quad[:3]]) == set(bnd.rdf.triples((None, None, None)))


def test_bundle_store_conf_with_two_dep_levels(custom_bundle):
    '''
    Test that transitive dependenices shared by multiple bundles are not included more
    than once
    '''
    imports_ctxid = 'http://example.org/imports'
    ctxid_1 = 'http://example.org/ctx1'
    ctxid_2 = 'http://example.org/ctx2'

    # Make a descriptor that includes ctx1 and the imports, but not ctx2
    d = Descriptor('test')
    d.includes.add(make_include_func(ctxid_1))
    d.includes.add(make_include_func(imports_ctxid))
    d.dependencies.add(DependencyDescriptor('dep'))
    d.dependencies.add(DependencyDescriptor('dep_dep'))

    dep_d = Descriptor('dep')
    dep_d.dependencies.add(DependencyDescriptor('dep_dep'))

    dep_dep_d = Descriptor('dep_dep')
    dep_dep_d.includes.add(make_include_func(ctxid_2))

    # Add some triples so the contexts aren't empty -- we can't save an empty context
    g = rdflib.ConjunctiveGraph()
    cg_1 = g.get_context(ctxid_1)
    cg_2 = g.get_context(ctxid_2)
    cg_1.add((URIRef('a'), URIRef('b'), URIRef('c')))
    cg_2.add((URIRef('d'), URIRef('e'), URIRef('f')))

    # End setup

    with custom_bundle(dep_dep_d, graph=g) as depdepbun, \
            custom_bundle(dep_d, bundles_directory=depdepbun.bundles_directory) as depbun, \
            custom_bundle(d, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert bnd.conf['rdf.store_conf'] == [
                ('FileStorageZODB', dict(
                    url=p(testbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                    read_only=True)),
                # dep
                ('owmeta_core_bds', dict(type='agg', conf=[
                    ('FileStorageZODB', dict(
                        url=p(depbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                        read_only=True)),
                    ('owmeta_core_bds', dict(type='agg', conf=[
                        ('FileStorageZODB', dict(
                            url=p(depdepbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                            read_only=True))
                    ]))
                ]))]


def test_bundle_store_conf_with_two_levels_excludes(custom_bundle):
    '''
    Test that transitive dependenices shared by multiple bundles are included more than
    once if there's an exclude that makes one of them different
    '''
    imports_ctxid = 'http://example.org/imports'
    ctxid_1 = 'http://example.org/ctx1'
    ctxid_2 = 'http://example.org/ctx2'

    # Make a descriptor that includes ctx1 and the imports, but not ctx2
    d = Descriptor('test')
    d.includes.add(make_include_func(ctxid_1))
    d.includes.add(make_include_func(imports_ctxid))
    d.dependencies.add(DependencyDescriptor('dep', excludes=(ctxid_1,)))
    d.dependencies.add(DependencyDescriptor('dep_dep'))

    dep_d = Descriptor('dep')
    dep_d.dependencies.add(DependencyDescriptor('dep_dep'))

    dep_dep_d = Descriptor('dep_dep')
    dep_dep_d.includes.add(make_include_func(ctxid_2))

    # Add some triples so the contexts aren't empty -- we can't save an empty context
    g = rdflib.ConjunctiveGraph()
    cg_1 = g.get_context(ctxid_1)
    cg_2 = g.get_context(ctxid_2)
    cg_1.add((URIRef('a'), URIRef('b'), URIRef('c')))
    cg_2.add((URIRef('d'), URIRef('e'), URIRef('f')))

    # End setup

    with custom_bundle(dep_dep_d, graph=g) as depdepbun, \
            custom_bundle(dep_d, bundles_directory=depdepbun.bundles_directory) as depbun, \
            custom_bundle(d, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert bnd.conf['rdf.store_conf'] == [
                ('FileStorageZODB', dict(
                    url=p(testbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                    read_only=True)),
                # dep
                ('owmeta_core_bds', dict(type='agg', conf=[
                    ('FileStorageZODB', dict(
                        url=p(depbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                        read_only=True)),
                    ('owmeta_core_bds', dict(type='agg', conf=[
                        ('FileStorageZODB', dict(
                            url=p(depdepbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                            read_only=True))
                    ]))
                ], excludes=[ctxid_1])),
                # depdep
                ('owmeta_core_bds', dict(type='agg', conf=[
                    ('FileStorageZODB', dict(
                        url=p(depdepbun.bundle_directory, BUNDLE_INDEXED_DB_NAME),
                        read_only=True))
                ]))]
