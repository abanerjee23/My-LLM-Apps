"""Generate docs/tarnfield_billing_policy.pdf.

The policy text lives here rather than as a loose binary so wording changes are
diffable and reviewable. Edit this file and re-run to regenerate:

    uv run --with reportlab python scripts/make_billing_policy.py

Design notes (BUILD_PLAN 6.2):
  * Billing territory only. Where returns are unavoidable -- refunds move money --
    this document covers the MECHANICS and explicitly defers ELIGIBILITY to the
    Returns and Exchanges Policy. That seam is the 2.1 agent boundary in prose,
    and it is what makes cross-domain eval cases real rather than contrived.
  * Some plausible questions are deliberately left unanswered so the refusal
    cases in section 4 of the build plan have something genuine to test:
    buy-now-pay-later, business invoicing and purchase orders, price-drop
    refunds, student discounts, and paying in instalments appear nowhere.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "docs" / "tarnfield_billing_policy.pdf"

_ss = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=_ss["Title"], fontName="Helvetica-Bold",
                       fontSize=15, alignment=TA_LEFT, spaceAfter=8)
INTRO = ParagraphStyle("i", parent=_ss["BodyText"], fontSize=8.5, leading=12,
                       textColor=colors.HexColor("#333333"), spaceAfter=10)
H = ParagraphStyle("h", parent=_ss["Heading2"], fontName="Helvetica-Bold",
                   fontSize=11, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("b", parent=_ss["BodyText"], fontSize=9, leading=13, spaceAfter=6)
CELL = ParagraphStyle("c", parent=_ss["BodyText"], fontSize=8.5, leading=11.5)
CELLB = ParagraphStyle("cb", parent=CELL, fontName="Helvetica-Bold")


def table(rows, widths):
    data = [[Paragraph(c, CELLB if i == 0 else CELL) for c in row]
            for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


W = A4[0] - 40 * mm
story = []
S = story.append

S(Paragraph("Tarnfield Running Co. Billing and Payments Policy", TITLE))
S(Paragraph(
    "Last updated 11 September 2026. This policy applies to orders placed on our online store. "
    "It does not affect your statutory rights. It covers how we take payment and how money finds "
    "its way back to you. <b>Whether an item can be returned or exchanged in the first place is "
    "covered by our Returns and Exchanges Policy, not this one.</b>", INTRO))

S(Paragraph("1. Key terms at a glance", H))
S(table([
    ["Item", "Our policy"],
    ["Payment methods", "Visa, Mastercard, American Express, Apple Pay, Google Pay, and Tarnfield gift cards. You can combine a gift card with one card."],
    ["When we take payment", "The full amount when you place the order, not when it ships. Your order confirmation is not confirmation of dispatch."],
    ["Currency", "Pounds sterling. If your card is held in another currency, your bank sets the conversion rate and any fee. We do not see or control either."],
    ["Pending charges", "An authorisation hold can show on your statement before the payment settles. These usually clear in 3 to 5 working days."],
    ["Failed payments", "The order is not placed and no stock is held. Nothing is owed. Try again or use another method."],
    ["Duplicate charges", "Email us both dates and amounts. We check and, where it is a genuine duplicate, refund within 5 working days."],
    ["Refund method", "Back to the original payment method. We cannot send a refund to a different card or account."],
    ["Refund timing", "Usually 5 to 10 working days to reach your account after a refund is approved. If nothing has arrived after two weeks, email us."],
    ["Delivery charges", "Not refunded if you change your mind. Refunded if the mistake was ours or the item was faulty."],
    ["Discount codes", "We refund what you actually paid, not the full price."],
    ["VAT receipts", "Emailed with your order confirmation. We can resend one at any time."],
    ["Contact", "billing@tarnfield.example. We usually reply within 24 to 48 hours, a little slower at weekends."],
], [40 * mm, W - 40 * mm]))

S(Paragraph("2. How and when we take payment", H))
S(Paragraph(
    "We take the full amount at checkout. Some banks first place an authorisation hold, which "
    "looks like a charge but has not settled yet. If your order fails after a hold has been "
    "placed, the hold is released by your bank, normally within 3 to 5 working days. We cannot "
    "release it sooner on your behalf.", BODY))
S(Paragraph(
    "Prices include VAT. The price you pay is the price shown at checkout. If a price changes "
    "after you have ordered, it does not affect an order already placed.", BODY))

S(Paragraph("3. If a payment fails", H))
S(Paragraph(
    "A failed payment usually means the card details did not match your bank's records, the card "
    "has expired, there were not enough funds, or your bank declined the transaction for its own "
    "reasons. We are not told which. Your order is not placed and we do not hold stock while you "
    "sort it out, so a popular size may sell out in the meantime.", BODY))

S(Paragraph("4. Pending charges and duplicates", H))
S(Paragraph(
    "If you see the same amount twice, it is usually an authorisation hold sitting alongside the "
    "settled payment, and it will disappear on its own. If both amounts are still there after 5 "
    "working days, email us with the dates, the amounts and the last four digits of the card. We "
    "will check our records and refund anything we have taken twice.", BODY))

S(Paragraph("5. Refunds: how the money moves", H))
S(Paragraph(
    "<b>This section covers only the mechanics.</b> Whether you are entitled to a refund at all "
    "is decided by our Returns and Exchanges Policy and, for some items, by the product listing. "
    "Some items are marked final sale and cannot be refunded unless faulty.", BODY))
S(table([
    ["Situation", "Where the money goes"],
    ["Paid by card", "Back to the same card. Refunds cannot be redirected to a different card."],
    ["Card expired or replaced", "Your bank will normally route the refund to your new card automatically. If it is rejected and comes back to us, we will contact you to arrange it."],
    ["Account closed", "The refund will bounce back to us. Email us and we will arrange an alternative. This can add two to three weeks."],
    ["Paid with a gift card", "Returned as gift card credit, not as cash."],
    ["Part gift card, part card", "Split in the same proportion as you paid. The gift card share comes back as credit, the card share to the card."],
    ["Paid by someone else (a gift)", "The refund goes to the original payment method, which means back to the person who paid. We can offer gift card credit instead if you would rather not involve them."],
], [45 * mm, W - 45 * mm]))

S(Paragraph("6. Gift cards and credit", H))
S(Paragraph(
    "Tarnfield gift cards do not expire and can be used across more than one order until the "
    "balance runs out. They cannot be exchanged for cash. If you lose the code and cannot supply "
    "it, we have no way to identify the card and cannot replace it, so keep the email safe.", BODY))

S(Paragraph("7. Discount codes and promotions", H))
S(Paragraph(
    "One discount code per order. Codes cannot be combined and cannot be applied after an order "
    "has been placed. When you return part of an order bought with a code, we refund what you "
    "actually paid for those items, not the full price.", BODY))
S(Paragraph(
    "If a code required a minimum spend and returning items takes the order below that minimum, "
    "the discount no longer applies to what you kept. We recalculate the order at the normal "
    "price and refund the difference, which will be less than you might expect. We will always "
    "show you this calculation before the refund is issued.", BODY))

S(Paragraph("8. Delivery charges", H))
S(Paragraph(
    "Delivery charges are refunded when the mistake was ours or the item was faulty. They are "
    "not refunded when you simply change your mind, even if the items themselves are refunded in "
    "full. Return postage is a separate matter and is covered by the Returns and Exchanges "
    "Policy.", BODY))

S(Paragraph("9. If you think a charge is wrong", H))
S(Paragraph(
    "Email billing@tarnfield.example before contacting your bank. Once a chargeback is raised, "
    "the payment is frozen while your bank investigates, and we are no longer able to refund you "
    "directly or to resolve it quickly. Almost every disputed charge we see turns out to be an "
    "authorisation hold or a second order placed by mistake, and both are quicker for us to sort "
    "out than for your bank.", BODY))
S(Paragraph("Please include your order number, the date and amount, and the last four digits of the card.", BODY))

S(Paragraph("10. Contact us", H))
S(Paragraph(
    "Email billing@tarnfield.example for anything about money. For questions about whether an "
    "item can be returned, exchanged or is still within its window, email "
    "support@tarnfield.example instead.", BODY))

SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    title="Tarnfield Running Co. Billing and Payments Policy",
).build(story)
print(f"wrote {OUT}")
