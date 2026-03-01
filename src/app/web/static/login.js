const form = document.getElementById("login-form");
const errorText = document.getElementById("login-error");
const createUserButton = document.getElementById("create-user-button");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorText.textContent = "";
  errorText.style.color = "#b22a2a";

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  const response = await fetch("/web/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    errorText.textContent = "Invalid username or password.";
    return;
  }

  window.location.href = "/app";
});

createUserButton.addEventListener("click", async () => {
  errorText.textContent = "";
  errorText.style.color = "#b22a2a";

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  if (!username || !password) {
    errorText.textContent = "Enter username and password first.";
    return;
  }

  const response = await fetch("/web/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    errorText.textContent = payload.detail || "Unable to create user.";
    return;
  }

  errorText.style.color = "#23723b";
  errorText.textContent = "User created. You can log in now.";
});
