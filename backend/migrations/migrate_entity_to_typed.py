#!/usr/bin/env python3
"""
migrate_entity_to_typed.py

One-time migration script that converts old :Entity and :Source nodes to the
new typed schema (Person, Organization, Technology, Concept, Event, Location,
Metric, Document) and reclassifies :RELATES_TO edges into typed relationship
labels (CAUSES, ENABLES, USES, etc.).

Usage:
    # Dry run (default) — shows what would change without modifying anything
    python -m migrations.migrate_entity_to_typed

    # Execute the migration
    python -m migrations.migrate_entity_to_typed --execute

    # Custom Neo4j connection
    python -m migrations.migrate_entity_to_typed --execute \\
        --uri bolt://localhost:7687 --user neo4j --password mypass

The migration is idempotent — running it twice will not create duplicates.
It preserves all existing properties and edges.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List

from neo4j import AsyncGraphDatabase

# Import the classifier and label resolver from the main module
sys.path.insert(0, ".")
from long_term_memory import (
    ENTITY_TYPES,
    _FACTUAL_REL_TYPES,
    _ENTITY_INDEX_NAMES,
    classify_relation,
    resolve_entity_label,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def migrate(
    uri: str = "bolt://localhost:7687",
    user: str = "neo4j",
    password: str = "research_pass",
    database: str = "neo4j",
    dry_run: bool = True,
) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        await driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s", uri)
    except Exception as exc:
        logger.error("Cannot connect to Neo4j: %s", exc)
        return

    async with driver.session(database=database) as session:
        # ── Step 1: Count existing nodes ──────────────────────────────────
        result = await session.run("MATCH (e:Entity) RETURN count(e) AS cnt")
        record = await result.single()
        entity_count = record["cnt"] if record else 0

        result = await session.run("MATCH (s:Source) RETURN count(s) AS cnt")
        record = await result.single()
        source_count = record["cnt"] if record else 0

        result = await session.run("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS cnt")
        record = await result.single()
        rel_count = record["cnt"] if record else 0

        logger.info(
            "Found: %d :Entity nodes, %d :Source nodes, %d :RELATES_TO edges",
            entity_count,
            source_count,
            rel_count,
        )

        if entity_count == 0 and source_count == 0:
            logger.info("Nothing to migrate.")
            await driver.close()
            return

        # ── Step 2: Migrate :Entity → typed labels ────────────────────────
        logger.info("Step 2: Migrating :Entity nodes to typed labels...")
        result = await session.run(
            "MATCH (e:Entity) "
            "RETURN e.id AS id, e.entity_type AS entity_type, e.name AS name"
        )
        records = await result.data()

        type_counts: Dict[str, int] = {}
        for rec in records:
            entity_type = rec.get("entity_type", "concept") or "concept"
            label = resolve_entity_label(entity_type)
            type_counts[label] = type_counts.get(label, 0) + 1

            if not dry_run:
                # Add the typed label and remove :Entity
                await session.run(
                    f"MATCH (e:Entity {{id: $id}}) "
                    f"SET e:{label} "
                    "REMOVE e:Entity",
                    id=rec["id"],
                )

        for label, count in sorted(type_counts.items()):
            action = "Would migrate" if dry_run else "Migrated"
            logger.info("  %s %d nodes → :%s", action, count, label)

        # ── Step 3: Migrate :Source → :Document ───────────────────────────
        if source_count > 0:
            logger.info("Step 3: Migrating :Source nodes to :Document...")
            if not dry_run:
                await session.run(
                    "MATCH (s:Source) "
                    "SET s:Document, "
                    "    s.doc_type = 'article', "
                    "    s.content_summary = '', "
                    "    s.session_id = '', "
                    "    s.retrieved_at = s.first_seen "
                    "REMOVE s:Source"
                )
            action = "Would migrate" if dry_run else "Migrated"
            logger.info("  %s %d :Source → :Document", action, source_count)
        else:
            logger.info("Step 3: No :Source nodes to migrate.")

        # ── Step 4: Reclassify :RELATES_TO edges ─────────────────────────
        if rel_count > 0:
            logger.info("Step 4: Reclassifying :RELATES_TO edges...")
            result = await session.run(
                "MATCH (s)-[r:RELATES_TO]->(t) "
                "RETURN r.id AS id, r.relation_type AS relation_type, "
                "       r.confidence AS confidence, r.evidence AS evidence, "
                "       r.session_id AS session_id, r.step_id AS step_id, "
                "       r.created_at AS created_at, "
                "       r.last_confirmed AS last_confirmed, "
                "       r.confirmation_count AS confirmation_count, "
                "       s.name AS source_name, t.name AS target_name"
            )
            records = await result.data()

            label_counts: Dict[str, int] = {}
            for rec in records:
                relation_type = rec.get("relation_type", "")
                new_label = classify_relation(relation_type)
                label_counts[new_label] = label_counts.get(new_label, 0) + 1

                if not dry_run and new_label != "RELATES_TO":
                    # Create new typed edge and delete old one
                    # We need to find source/target by name across all typed labels
                    all_labels = "|".join(ENTITY_TYPES)
                    await session.run(
                        f"MATCH (s:{all_labels} {{name: $src_name}}) "
                        f"MATCH (t:{all_labels} {{name: $tgt_name}}) "
                        f"CREATE (s)-[:{new_label} {{"
                        "  id: $id, relation_type: $relation_type, "
                        "  relationship_label: $new_label, "
                        "  confidence: $confidence, evidence: $evidence, "
                        "  session_id: $session_id, step_id: $step_id, "
                        "  created_at: $created_at, last_confirmed: $last_confirmed, "
                        "  confirmation_count: $confirmation_count"
                        "}]->(t)",
                        src_name=rec["source_name"],
                        tgt_name=rec["target_name"],
                        id=rec["id"],
                        relation_type=relation_type,
                        new_label=new_label,
                        confidence=rec.get("confidence", 0.8),
                        evidence=rec.get("evidence", ""),
                        session_id=rec.get("session_id", ""),
                        step_id=rec.get("step_id", 0),
                        created_at=rec.get("created_at", ""),
                        last_confirmed=rec.get("last_confirmed", ""),
                        confirmation_count=rec.get("confirmation_count", 1),
                    )

            # Delete old RELATES_TO edges that were reclassified
            reclassified = sum(
                c for label, c in label_counts.items() if label != "RELATES_TO"
            )
            if not dry_run and reclassified > 0:
                # Delete RELATES_TO edges whose IDs were reclassified
                reclassified_ids = [
                    rec["id"]
                    for rec in records
                    if classify_relation(rec.get("relation_type", "")) != "RELATES_TO"
                ]
                if reclassified_ids:
                    await session.run(
                        "MATCH ()-[r:RELATES_TO]->() " "WHERE r.id IN $ids DELETE r",
                        ids=reclassified_ids,
                    )

            # Add relationship_label property to remaining RELATES_TO edges
            if not dry_run:
                await session.run(
                    "MATCH ()-[r:RELATES_TO]->() "
                    "WHERE r.relationship_label IS NULL "
                    "SET r.relationship_label = 'RELATES_TO'"
                )

            for label, count in sorted(label_counts.items()):
                action = "Would reclassify" if dry_run else "Reclassified"
                logger.info("  %s %d edges → :%s", action, count, label)
        else:
            logger.info("Step 4: No :RELATES_TO edges to reclassify.")

        # ── Step 5: Verify ────────────────────────────────────────────────
        if not dry_run:
            logger.info("Step 5: Verification...")

            # Check no :Entity nodes remain
            result = await session.run("MATCH (e:Entity) RETURN count(e) AS cnt")
            record = await result.single()
            remaining_entities = record["cnt"] if record else 0

            # Check no :Source nodes remain
            result = await session.run("MATCH (s:Source) RETURN count(s) AS cnt")
            record = await result.single()
            remaining_sources = record["cnt"] if record else 0

            # Count typed nodes
            for label in ENTITY_TYPES:
                result = await session.run(f"MATCH (e:{label}) RETURN count(e) AS cnt")
                record = await result.single()
                count = record["cnt"] if record else 0
                if count > 0:
                    logger.info("  :%s — %d nodes", label, count)

            result = await session.run("MATCH (d:Document) RETURN count(d) AS cnt")
            record = await result.single()
            doc_count = record["cnt"] if record else 0
            if doc_count > 0:
                logger.info("  :Document — %d nodes", doc_count)

            if remaining_entities > 0:
                logger.warning(
                    "  ⚠ %d :Entity nodes still remain! "
                    "These may have had unrecognized entity_type values.",
                    remaining_entities,
                )
            else:
                logger.info("  ✓ All :Entity nodes migrated successfully.")

            if remaining_sources > 0:
                logger.warning("  ⚠ %d :Source nodes still remain!", remaining_sources)
            else:
                logger.info("  ✓ All :Source nodes migrated successfully.")
        else:
            logger.info("\n--- DRY RUN complete. No changes were made. ---")
            logger.info("Run with --execute to apply the migration.")

    await driver.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate :Entity/:Source nodes to typed graph schema"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the migration (default is dry-run)",
    )
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j username")
    parser.add_argument("--password", default="research_pass", help="Neo4j password")
    parser.add_argument("--database", default="neo4j", help="Neo4j database name")
    args = parser.parse_args()

    asyncio.run(
        migrate(
            uri=args.uri,
            user=args.user,
            password=args.password,
            database=args.database,
            dry_run=not args.execute,
        )
    )


if __name__ == "__main__":
    main()
