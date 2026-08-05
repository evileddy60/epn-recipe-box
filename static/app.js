document.addEventListener("click", (event) => {
  const button = event.target.closest(".copy-share-link");
  if (!button || !navigator.clipboard) return;
  navigator.clipboard.writeText(button.dataset.shareUrl || "");
});
