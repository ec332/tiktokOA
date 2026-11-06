# Items and accounts — user story

> Draft for review. Written from the product's README and dashboard description only,
> not from its code. Status: awaiting the product owner's approval.

## Logging in

As a registered user, I want to log in with my email and password so that I can manage my items.

- A user who gives the right email and password receives an access token.
- A wrong password, or an email nobody has registered, is refused without saying which of the two was wrong.
- An account that an administrator has deactivated cannot be used to log in.

## Staying logged in

As a user, I don't want to log in every time I open the dashboard.

- An access token stays valid for about a week, then expires.
- An expired token is refused; the user has to log in again.

## My items

As a user, I keep a list of items, each with a title and an optional description.

- I can create an item; it belongs to me.
- I can see, change and delete my own items.
- I cannot see, change or delete items that belong to someone else.
- An administrator can see and manage every item.

## Item titles

- Every item must have a title.
- A title can't be too long; the dashboard allows titles of up to 255 characters.

## Browsing items

As a user with many items, I want to page through them.

- The item list shows the newest items first.
- I can ask for a page of items by saying how many to skip and how many to show.
- Each response also tells me how many items I have in total.
- A page shows a reasonable number of items by default.
