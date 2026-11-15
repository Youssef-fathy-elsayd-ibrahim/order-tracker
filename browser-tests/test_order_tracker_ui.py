from playwright.sync_api import Page, expect


def test_home_displays_seeded_orders(page: Page, app_url: str):
    page.goto(app_url)

    orders = page.get_by_role("region", name="Orders")
    expect(orders.locator("article")).to_have_count(3)
    expect(orders.get_by_text("Notebook", exact=True)).to_be_visible()
    expect(orders.get_by_text("Headphones", exact=True)).to_be_visible()


def test_create_order_and_check_its_status(page: Page, app_url: str):
    page.goto(app_url)
    page.get_by_label("Customer").fill("Morgan")
    page.get_by_label("Item").fill("Thermos")
    page.get_by_label("Priority").select_option("standard")
    page.get_by_role("button", name="Create order").click()

    order = page.locator("#orders article").filter(has_text="Thermos")
    expect(order).to_be_visible()
    expect(order).to_contain_text("Morgan")
    order.get_by_role("button", name="Check status for Thermos").click()
    expect(page.get_by_role("status")).to_have_text(
        "Thermos for Morgan: received"
    )


def test_failed_status_lookup_announces_error(page: Page, app_url: str):
    page.route(
        "**/api/orders/standard-1001",
        lambda route: route.fulfill(status=503, body="Unavailable"),
    )
    page.goto(app_url)
    page.get_by_role("button", name="Check status for Notebook").click()

    expect(page.get_by_role("alert")).to_have_text(
        "Could not check this order (HTTP 503). Please try again."
    )


def test_failed_initial_order_load_is_visible(page: Page, app_url: str):
    page.route(
        "**/api/orders",
        lambda route: route.fulfill(status=503, body="Unavailable"),
    )
    page.goto(app_url)

    expect(page.get_by_role("alert")).to_have_text(
        "Could not load orders (HTTP 503). Please try again."
    )
    expect(page.locator("#orders article")).to_have_count(0)


def test_failed_order_creation_is_announced_and_can_be_retried(
    page: Page, app_url: str
):
    def fail_create(route):
        if route.request.method == "POST":
            route.fulfill(status=503, body="Unavailable")
        else:
            route.continue_()

    page.route("**/api/orders", fail_create)
    page.goto(app_url)
    page.get_by_label("Customer").fill("Morgan")
    page.get_by_label("Item").fill("Thermos")
    submit = page.get_by_role("button", name="Create order")
    submit.click()

    expect(page.get_by_role("alert")).to_have_text(
        "Could not create order (HTTP 503). Please try again."
    )
    expect(submit).to_be_enabled()
    expect(page.get_by_label("Customer")).to_have_value("Morgan")


def test_user_markup_is_rendered_as_text(page: Page, app_url: str):
    unsafe_customer = '<script>window.__orderTrackerXss = "customer"</script>'
    unsafe_item = "<img src=x onerror=window.__orderTrackerXss='item'>"

    page.goto(app_url)
    page.get_by_label("Customer").fill(unsafe_customer)
    page.get_by_label("Item").fill(unsafe_item)
    page.get_by_role("button", name="Create order").click()

    order = page.locator("#orders article").filter(has_text=unsafe_customer)
    expect(order).to_be_visible()
    expect(order).to_contain_text(unsafe_item)
    expect(page.locator("#orders script, #orders img")).to_have_count(0)
    assert page.evaluate("window.__orderTrackerXss") is None
