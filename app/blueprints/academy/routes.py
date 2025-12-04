from datetime import datetime

from flask import g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import ContentItem, UserContentProgress

from . import academy_bp


@academy_bp.route("/dashboard")
@login_required
def dashboard():
    session = g.db
    stories = (
        session.query(ContentItem)
        .filter_by(content_type="STORY", is_published=True)
        .order_by(ContentItem.created_at.desc())
        .limit(12)
        .all()
    )
    feed_items = (
        session.query(ContentItem)
        .filter(ContentItem.content_type != "STORY", ContentItem.is_published.is_(True))
        .order_by(ContentItem.created_at.desc())
        .all()
    )
    progress_records = (
        session.query(UserContentProgress)
        .filter_by(user_id=current_user.id)
        .all()
    )
    progress_map = {progress.content_item_id: progress for progress in progress_records}
    read_count = sum(1 for record in progress_records if record.is_read)

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_reads = (
        session.query(UserContentProgress)
        .filter(
            UserContentProgress.user_id == current_user.id,
            UserContentProgress.is_read.is_(True),
            UserContentProgress.read_at.isnot(None),
            UserContentProgress.read_at >= month_start,
        )
        .count()
    )

    return render_template(
        "academy/dashboard.html",
        stories=stories,
        feed=feed_items,
        progress_map=progress_map,
        read_count=read_count,
        monthly_reads=monthly_reads,
    )


@academy_bp.route("/item/<int:item_id>")
@login_required
def view_item(item_id):
    session = g.db
    item = session.get(ContentItem, item_id)
    if not item or (not item.is_published and not current_user.is_admin):
        return render_template(
            "404.html",
            page_title="Страницата не е достъпна",
            message="Нямаме публикувано съдържание с това ID или го редактираме. Ако ти си админ, виж го отново след 'Preview'.",
        ), 404

    progress = (
        session.query(UserContentProgress)
        .filter_by(user_id=current_user.id, content_item_id=item.id)
        .first()
    )
    return render_template("academy/item_detail.html", item=item, progress=progress)


@academy_bp.route("/story/<int:item_id>")
@login_required
def story_view(item_id):
    session = g.db
    item = session.get(ContentItem, item_id)
    if not item or item.content_type != "STORY" or not item.is_published:
        return redirect(url_for("academy.dashboard"))
    return render_template("academy/story_view.html", item=item)


@academy_bp.route("/api/mark-read/<int:item_id>", methods=["POST"])
@login_required
def mark_read(item_id):
    session = g.db
    item = session.get(ContentItem, item_id)
    if not item:
        return jsonify({"status": "error", "message": "Item not found"}), 404
    progress = (
        session.query(UserContentProgress)
        .filter_by(user_id=current_user.id, content_item_id=item.id)
        .first()
    )
    if not progress:
        progress = UserContentProgress(user_id=current_user.id, content_item_id=item.id)
    progress.is_read = True
    progress.read_at = datetime.utcnow()
    session.add(progress)
    session.commit()
    return jsonify({"status": "success", "is_read": True})


@academy_bp.route("/api/react/<int:item_id>", methods=["POST"])
@login_required
def react(item_id):
    session = g.db
    reaction = request.form.get("reaction") or request.json and request.json.get("reaction") or "like"
    item = session.get(ContentItem, item_id)
    if not item:
        return jsonify({"status": "error", "message": "Item not found"}), 404
    progress = (
        session.query(UserContentProgress)
        .filter_by(user_id=current_user.id, content_item_id=item.id)
        .first()
    )
    if not progress:
        progress = UserContentProgress(user_id=current_user.id, content_item_id=item.id)
    progress.reaction = reaction
    session.add(progress)
    session.commit()
    return jsonify({"status": "success", "reaction": reaction})
