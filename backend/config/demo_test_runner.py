"""Run rollback-only Django tests against the existing isolated database."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestSuite

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.runner import DiscoverRunner
from django.db.migrations.executor import MigrationExecutor

from .postgres_demo import DATABASE_NAME


class IsolatedDemoRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        connection = connections["default"]
        connection.ensure_connection()
        if connection.settings_dict["NAME"] != DATABASE_NAME:
            raise ImproperlyConfigured("Pruebas fuera de la base demo.")
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise ImproperlyConfigured("La base demo tiene migraciones pendientes.")
        return []

    def teardown_databases(self, old_config, **kwargs):
        connections.close_all()

    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)

        def validate(node):
            if isinstance(node, TestSuite):
                for child in node:
                    validate(child)
            elif not isinstance(node, SimpleTestCase) or (
                isinstance(node, TransactionTestCase) and not isinstance(node, TestCase)
            ):
                raise ImproperlyConfigured(
                    f"Prueba excluida porque no garantiza reversión mediante TestCase: {node.id()}"
                )

        validate(suite)
        return suite

    def run_tests(self, *args, **kwargs):
        if self.parallel != 1:
            raise ImproperlyConfigured("Ejecute las pruebas de la demo sin procesos paralelos.")
        for key in tuple(os.environ):
            if key.startswith("SIGELC_MUESTRA"):
                del os.environ[key]
        with TemporaryDirectory(prefix="sigelc-pruebas-") as folder:
            with override_settings(MEDIA_ROOT=Path(folder)):
                return super().run_tests(*args, **kwargs)
