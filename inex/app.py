from dataclasses import dataclass

from inex.clients.npm import NpmClient
from inex.config import REPO_ROOT, Settings
from inex.services.classification import ClassificationService
from inex.services.collection import CollectionService
from inex.services.imports import ImportService
from inex.services.linkage import LinkageService
from inex.services.maintenance import MaintenanceService
from inex.services.queries import QueryService
from inex.store.database import Store

GUIDE_PATH = REPO_ROOT / "profiles" / "inex_guide.md"


@dataclass
class App:
    settings: Settings
    collection: CollectionService
    classification: ClassificationService
    imports: ImportService
    linkage: LinkageService
    queries: QueryService
    maintenance: MaintenanceService

    def close(self) -> None:
        self.queries.store.close()


def build_app(settings: Settings) -> App:
    store = Store(settings.database)
    collection = CollectionService(store, settings.github_token)
    classification = ClassificationService(
        store,
        GUIDE_PATH,
        settings.classifier,
        settings.temperature,
        workers=settings.classify_workers,
        retries=settings.classify_retries,
        ollama_base=settings.ollama_base,
    )
    return App(
        settings=settings,
        collection=collection,
        classification=classification,
        imports=ImportService(store, collection, classification),
        linkage=LinkageService(store, NpmClient(settings.npm_cache_dir)),
        queries=QueryService(store, settings.exports_dir),
        maintenance=MaintenanceService(store),
    )
