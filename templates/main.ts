// Make sure to have https://cdn.socket.io/4.7.2/socket.io.min.js loaded in HTML

window.onload = function () {
  // Connect to Socket.IO server
  const socket = (window as any).io({
    transports: ["websocket", "polling"],
  });

  // Listen for uuid_update event
  socket.on("uuid_update", function (data: {uuid: string, email?: string}) {
    (document.getElementById("uuid") as HTMLInputElement).value = data.uuid;
    (document.getElementById("email") as HTMLInputElement).value = data.email || "";
  });

  // Optionally: Take a Photo button handler
  const takePhotoBtn = document.getElementById('takePhotoBtn');
  if (takePhotoBtn) {
    takePhotoBtn.addEventListener('click', function() {
      fetch('http://192.168.40.223/capture')
        .then(response => response.blob())
        .then(blob => {
          // Example: download the photo
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'esp32-photo.jpg';
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        })
        .catch(err => alert('Capture failed: ' + err));
    });
  }
};