from alembic import op
import sqlalchemy as sa
revision = "026_push_subscriptions"
down_revision = "025_order_templates"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("push_subscriptions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("endpoint", sa.String(512), nullable=False), sa.Column("p256dh", sa.String(500), nullable=False), sa.Column("auth", sa.String(500), nullable=False), sa.Column("expiration_time", sa.DateTime(), nullable=True), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"))
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
def downgrade():
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
