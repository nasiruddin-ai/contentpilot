"""add research analysis, topics and opportunities

Revision ID: 5a8b7b636202
Revises: 194d5b7e1e4d
Create Date: 2026-09-24 15:25:45.392190
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5a8b7b636202'
down_revision: str | None = '194d5b7e1e4d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector ships with the pgvector/pgvector image; managed Postgres must allow this extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('opportunity_runs',
    sa.Column('brand_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='opportunity_run_status', native_enum=False, create_constraint=True, length=40), server_default='queued', nullable=False),
    sa.Column('requested_count', sa.Integer(), nullable=False),
    sa.Column('items_analyzed', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('topics_created', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('opportunities_created', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('opportunities_rejected', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_opportunity_runs_brand_id_brands'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opportunity_runs'))
    )
    op.create_index(op.f('ix_opportunity_runs_brand_id'), 'opportunity_runs', ['brand_id'], unique=False)
    op.create_table('topics',
    sa.Column('brand_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_topics_brand_id_brands'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topics'))
    )
    op.create_index(op.f('ix_topics_brand_id'), 'topics', ['brand_id'], unique=False)
    op.create_table('content_opportunities',
    sa.Column('brand_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('topic_id', sa.Uuid(), nullable=True),
    sa.Column('topic', sa.String(length=120), nullable=False),
    sa.Column('angle', sa.String(length=300), nullable=False),
    sa.Column('why_now', sa.Text(), nullable=False),
    sa.Column('audience', sa.String(length=300), nullable=False),
    sa.Column('recommended_format', sa.Enum('text_post', 'carousel', 'thread', 'image_post', 'short_video', 'article', name='content_format', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('recommended_platforms', postgresql.ARRAY(sa.String(length=20)), nullable=False),
    sa.Column('content_pillar', sa.Enum('educational', 'opinion', 'story', 'how_to', 'case_study', 'comparison', 'industry_insight', 'faq', 'behind_the_scenes', 'promotion', 'community', name='opportunity_pillar', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('source_ids', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('relevance_score', sa.Integer(), nullable=False),
    sa.Column('freshness_score', sa.Integer(), nullable=False),
    sa.Column('brand_fit_score', sa.Integer(), nullable=False),
    sa.Column('novelty_score', sa.Integer(), nullable=False),
    sa.Column('priority_score', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('new', 'saved', 'dismissed', 'used', name='opportunity_status', native_enum=False, create_constraint=True, length=40), server_default='new', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('brand_fit_score BETWEEN 0 AND 100', name=op.f('ck_content_opportunities_brand_fit_score_range')),
    sa.CheckConstraint('freshness_score BETWEEN 0 AND 100', name=op.f('ck_content_opportunities_freshness_score_range')),
    sa.CheckConstraint('novelty_score BETWEEN 0 AND 100', name=op.f('ck_content_opportunities_novelty_score_range')),
    sa.CheckConstraint('priority_score BETWEEN 0 AND 100', name=op.f('ck_content_opportunities_priority_score_range')),
    sa.CheckConstraint('relevance_score BETWEEN 0 AND 100', name=op.f('ck_content_opportunities_relevance_score_range')),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_content_opportunities_brand_id_brands'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['opportunity_runs.id'], name=op.f('fk_content_opportunities_run_id_opportunity_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], name=op.f('fk_content_opportunities_topic_id_topics'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_opportunities'))
    )
    op.create_index(op.f('ix_content_opportunities_brand_id'), 'content_opportunities', ['brand_id'], unique=False)
    op.create_index(op.f('ix_content_opportunities_priority_score'), 'content_opportunities', ['priority_score'], unique=False)
    op.create_index(op.f('ix_content_opportunities_topic_id'), 'content_opportunities', ['topic_id'], unique=False)
    op.create_table('research_item_topics',
    sa.Column('research_item_id', sa.Uuid(), nullable=False),
    sa.Column('topic_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['research_item_id'], ['research_items.id'], name=op.f('fk_research_item_topics_research_item_id_research_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], name=op.f('fk_research_item_topics_topic_id_topics'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('research_item_id', 'topic_id', name=op.f('pk_research_item_topics'))
    )
    op.create_index(op.f('ix_research_item_topics_topic_id'), 'research_item_topics', ['topic_id'], unique=False)
    op.add_column('research_items', sa.Column('topics', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('research_items', sa.Column('keywords', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('research_items', sa.Column('entities', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('research_items', sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('research_items', sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True))
    op.create_index(op.f('ix_research_items_analyzed_at'), 'research_items', ['analyzed_at'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_research_items_analyzed_at'), table_name='research_items')
    op.drop_column('research_items', 'embedding')
    op.drop_column('research_items', 'analyzed_at')
    op.drop_column('research_items', 'entities')
    op.drop_column('research_items', 'keywords')
    op.drop_column('research_items', 'topics')
    op.drop_index(op.f('ix_research_item_topics_topic_id'), table_name='research_item_topics')
    op.drop_table('research_item_topics')
    op.drop_index(op.f('ix_content_opportunities_topic_id'), table_name='content_opportunities')
    op.drop_index(op.f('ix_content_opportunities_priority_score'), table_name='content_opportunities')
    op.drop_index(op.f('ix_content_opportunities_brand_id'), table_name='content_opportunities')
    op.drop_table('content_opportunities')
    op.drop_index(op.f('ix_topics_brand_id'), table_name='topics')
    op.drop_table('topics')
    op.drop_index(op.f('ix_opportunity_runs_brand_id'), table_name='opportunity_runs')
    op.drop_table('opportunity_runs')
    # ### end Alembic commands ###
